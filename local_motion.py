"""Short obstacle recovery from observed rays and same-floor navigation only."""
import collections
import math
from navigation import flat_distance,wrap,segment_distance


class MotionRecovery:
    def __init__(self,steer):
        self.steer=steer;self.reset()

    def reset(self):
        self.history=collections.deque();self.detour=None;self.blocked_reason=None;self.last_danger_t=-1000
        self.travel_history=collections.deque()

    def _floor(self,s,nav):
        p=s['player']['p'];areas=[]
        for observed in s.get('nearby_nav',[]):
            a=nav.areas.get(observed['id'])
            if not a or observed['blocked'] or observed['damaging'] or 'nw' not in a:continue
            if max(abs(a['nw'][2]-p[2]),abs(a['se'][2]-p[2]))>18:continue
            if all(a['nw'][i]-110<=p[i]<=a['se'][i]+110 for i in (0,1)):areas.append(a)
        return areas

    def _supported(self,start,end,areas):
        # Check the whole short corridor, including player-width clearance.
        # A clear horizontal ray alone does not rule out a drop or nav gap.
        for j in range(1,9):
            p=[start[i]+(end[i]-start[i])*j/8 for i in (0,1)]
            for dx,dy in ((0,0),(-14,-14),(-14,14),(14,-14),(14,14)):
                q=[p[0]+dx,p[1]+dy]
                if not any(all(a['nw'][i]<=q[i]<=a['se'][i] for i in (0,1)) for a in areas):return False
        return True

    def _clear(self,s,heading,length):
        rays=s.get('obstacles',[]);relative=wrap(heading-s['player']['angles'][1])
        ray=min(rays,key=lambda r:abs(wrap(r['angle']-relative)),default=None)
        if not ray or abs(wrap(ray['angle']-relative))>16:return False
        if ray['fraction']*160<length+24:return False
        for side in (-30,30):
            flank=min(rays,key=lambda r:abs(wrap(r['angle']-ray['angle']-side)),default=None)
            if not flank or flank['fraction']*160<40:return False
        return True

    def _endpoint(self,p,keys,yaw,length):
        forward=int('w' in keys)-int('s' in keys);left=int('a' in keys)-int('d' in keys)
        a=math.radians(yaw);dx=forward*math.cos(a)-left*math.sin(a);dy=forward*math.sin(a)+left*math.cos(a)
        scale=length/max(.001,math.hypot(dx,dy))
        return [p[0]+dx*scale,p[1]+dy*scale,p[2]]

    def avoid_calm_witches(self,s,action,nav):
        p=s['player'];start=p['p'];moving={'w','a','s','d'}
        if (not moving.intersection(action['keys']) or p['incap'] or p['pinned'] or p['dead']
            or p.get('on_ladder') or abs(p.get('velocity',[0,0,0])[2])>25):return action
        witches=[e for e in s.get('enemies',[]) if e['type']==7 and e.get('rage',0)<.8
            and e['health']>0 and not e.get('incap') and abs(e['p'][2]-start[2]-40)<110]
        if not witches:return action
        yaw=p['angles'][1]-action['dx']*.066
        intended=self._endpoint(start,action['keys'],yaw,64)
        def safe(point):
            return all(segment_distance([*e['p'][:2],start[2]],start,point)>=min(200,flat_distance(start,e['p'])-2) for e in witches)
        if safe(intended):return action
        # A nav rectangle may be too large for graph costs to route around an
        # individual Witch. Try only observed clear, supported sidesteps.
        areas=self._floor(s,nav);choices=[]
        for ray in s.get('obstacles',[]):
            heading=p['angles'][1]+ray['angle']
            point=[start[0]+64*math.cos(math.radians(heading)),start[1]+64*math.sin(math.radians(heading)),start[2]]
            keys=self.steer(start,point,yaw,12);actual=self._endpoint(start,keys,yaw,64)
            actual_heading=math.degrees(math.atan2(actual[1]-start[1],actual[0]-start[0]))
            if not safe(actual) or not self._supported(start,actual,areas) or not self._clear(s,actual_heading,64):continue
            if any(flat_distance(actual,e['p'])<flat_distance(start,e['p'])-5 for e in s['enemies'] if e['type']==8):continue
            choices.append((flat_distance(actual,intended),keys))
        extra=[k for k in action['keys'] if k not in moving|{'space','shift'}]
        if choices:
            return dict(action,keys=(min(choices,key=lambda c:c[0])[1]+extra)[:4],witch_avoidance='Observed clear sidestep around calm Witch')
        self.blocked_reason='Calm Witch blocks the observed route; no clear supported sidestep'
        return dict(action,keys=extra,witch_avoidance='Stop approaching the calm Witch for route review')

    def adjust(self,s,action,nav):
        p=s['player'];t=s['t'];self.blocked_reason=None
        if (p['dead'] or p['incap'] or p['pinned'] or p.get('immobilized') or p.get('on_ladder')
            or abs(p.get('velocity',[0,0,0])[2])>25
            or action['task'] not in ('route','escape','evade','hold','stage')):
            self.reset();return action
        moving=bool(set(action['keys'])&{'w','a','s','d'})
        self.history.append((t,list(p['p']),moving))
        while self.history and t-self.history[0][0]>6.5:self.history.popleft()
        active=[h for h in self.history if h[2]]
        if action['task'] in ('escape','evade') or p['area_damaging'] or p['on_fire']:self.last_danger_t=t
        danger=t-self.last_danger_t<2.5
        # Repeated portal approaches can move enough to evade the short stall
        # check while never leaving one fence. Keep a separate travel window
        # across Jev's route/hold changes; actual healing and incap reset it.
        self.travel_history.append((t,list(p['p']),moving and action['task']=='route'))
        while self.travel_history and t-self.travel_history[0][0]>30.5:self.travel_history.popleft()
        travel=[h for h in self.travel_history if h[2]]
        if moving and action['task']=='route' and not danger and len(travel)>=20 and t-travel[0][0]>=30:
            span=[max(h[1][i] for h in travel)-min(h[1][i] for h in travel) for i in range(3)]
            if math.hypot(span[0],span[1])<140 and span[2]<45:
                self.blocked_reason='Travel is cycling around the same obstruction across tactical decisions'
        # Measure actual movement requests across brief route/hold/evade
        # changes, rather than restarting the clock for every Jev choice.
        limit=3 if danger else 6
        if moving and active and t-active[0][0]>=limit and len(active)>=10 and max(math.dist(p['p'],h[1]) for h in active)<25:
            self.blocked_reason='Movement inputs remained blocked across tactical decisions'
        if not moving or not action.get('waypoint'):return action
        floor_flags=nav.areas.get(p['area'],{}).get('attr',0)|action['waypoint'].get('attr',0)
        if floor_flags&4:
            # PRECISE areas explicitly forbid obstacle-adjustment detours.
            # Retain the stall handoff above, but follow the observed corridor.
            self.detour=None
            return action
        areas=self._floor(s,nav)
        if self.detour:
            point,deadline=self.detour;d=flat_distance(p['p'],point)
            heading=math.degrees(math.atan2(point[1]-p['p'][1],point[0]-p['p'][0]))
            if d<14 or t>deadline or not self._supported(p['p'],point,areas) or not self._clear(s,heading,d):self.detour=None
        recent=[h for h in active if t-h[0]<=1.5]
        stalled=recent and t-recent[0][0]>=.8 and len(recent)>=5 and max(flat_distance(p['p'],h[1]) for h in recent)<12
        wanted=action['waypoint']['p']
        heading=math.degrees(math.atan2(wanted[1]-p['p'][1],wanted[0]-p['p'][0]))
        relative=wrap(heading-p['angles'][1])
        ray=min(s.get('obstacles',[]),key=lambda r:abs(wrap(r['angle']-relative)),default=None)
        solid_ahead=(danger and ray is not None and abs(wrap(ray['angle']-relative))<=16
            and (ray.get('hit_type')=='worldspawn' or ray.get('hit_type','').startswith('prop_'))
            and ray['fraction']*160<min(64,flat_distance(p['p'],wanted))+24)
        if not self.detour and (stalled or solid_ahead):
            choices=[]
            tanks=[e for e in s['enemies'] if e['type']==8]
            for ray in s.get('obstacles',[]):
                yaw=p['angles'][1]+ray['angle'];deviation=abs(wrap(yaw-heading))
                if deviation>120 or not self._clear(s,yaw,64):continue
                point=[p['p'][0]+64*math.cos(math.radians(yaw)),p['p'][1]+64*math.sin(math.radians(yaw)),p['p'][2]]
                if not self._supported(p['p'],point,areas):continue
                if any(flat_distance(point,e['p'])<flat_distance(p['p'],e['p'])-5 for e in tanks):continue
                choices.append((flat_distance(point,wanted)+deviation*.3,point))
            if choices:self.detour=(min(choices,key=lambda row:row[0])[1],t+1.1)
        if not self.detour:
            if solid_ahead:self.blocked_reason='Observed solid obstruction blocks retreat; choose another validated route'
            return action
        predicted_yaw=p['angles'][1]-action['dx']*.066
        keys=self.steer(p['p'],self.detour[0],predicted_yaw,12)
        distance_to_goal=flat_distance(p['p'],self.detour[0])
        actual=self._endpoint(p['p'],keys,predicted_yaw,min(64,distance_to_goal))
        actual_heading=math.degrees(math.atan2(actual[1]-p['p'][1],actual[0]-p['p'][0]))
        closer_to_tank=any(flat_distance(actual,e['p'])<flat_distance(p['p'],e['p'])-5 for e in s['enemies'] if e['type']==8)
        if closer_to_tank or not self._supported(p['p'],actual,areas) or not self._clear(s,actual_heading,min(64,distance_to_goal)):
            self.detour=None
            if solid_ahead:self.blocked_reason='Observed solid obstruction blocks retreat; no clear supported sidestep'
            return action
        extra=[k for k in action['keys'] if k not in ('w','a','s','d','shift','space')]
        return dict(action,keys=(keys+extra)[:4],local_detour={'p':self.detour[0],
            'reason':'Observed obstruction during retreat; clear same-floor sidestep' if solid_ahead else 'Observed movement stall; clear same-floor sidestep'})
