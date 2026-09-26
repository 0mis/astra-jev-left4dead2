"""Routes over the game's observed navigation graph; never changes game state."""
import heapq
import math
import pathlib
import json
import datetime
from observe_game import EMS,read_json

def distance(a,b): return math.dist(a,b)
def flat_distance(a,b): return math.hypot(a[0]-b[0],a[1]-b[1])
def segment_distance(point,a,b):
    delta=[b[i]-a[i] for i in range(3)];length=sum(v*v for v in delta)
    t=max(0,min(1,sum((point[i]-a[i])*delta[i] for i in range(3))/length)) if length else 0
    return distance(point,[a[i]+t*delta[i] for i in range(3)])
def wrap(a): return (a+180)%360-180
def look(eye,target):
    dx,dy,dz=(b-a for a,b in zip(eye,target))
    return [-math.degrees(math.atan2(dz,math.hypot(dx,dy))),math.degrees(math.atan2(dy,dx))]

class NoRouteError(RuntimeError):pass

def ladder_connections(ladders,areas):
    """Use all engine-confirmed top landings, including side exits."""
    edges={}
    for ladder in ladders:
        bottom=ladder['bottom_area']
        tops=set(ladder.get('top_areas',[]))|{ladder.get('top_area')}
        for top in tops:
            if bottom not in areas or top not in areas or top==bottom:continue
            connection=dict(ladder,top_area=top)
            edges[(bottom,top)]=(connection,True)
            edges[(top,bottom)]=(connection,False)
    return edges

def active_recoveries(rows,mapname,game_t):
    # A stall can be a door, combat, or a controller error. Only a reviewed
    # solid obstruction is reusable across controllers, and even it expires.
    return {tuple(r['edge']) for r in rows if r.get('map')==mapname
        and r.get('confirmed_solid') and isinstance(r.get('game_t'),(int,float))
        and r['game_t']<=game_t<=r.get('expires_game_t',r['game_t'])}

def reviewed_links(review,state,areas):
    if not (isinstance(review,dict) and review.get('map')==state['map']
        and review.get('round_id')==state.get('round_id')
        and isinstance(review.get('created_game_t'),(int,float))
        and isinstance(review.get('expires_game_t'),(int,float))
        and review['created_game_t']<=state['t']<=review['expires_game_t']
        and review['expires_game_t']-review['created_game_t']<=7200):return set(),{}
    def edge(row):
        return (isinstance(row,list) and len(row)==2 and all(type(i) is int and i in areas for i in row)
            and row[1] in areas[row[0]]['adj'])
    blocked={tuple(row) for row in review.get('blocked_edges',[]) if edge(row)}
    drops={tuple(row['edge']):row['max_drop'] for row in review.get('drop_edges',[])
        if isinstance(row,dict) and edge(row.get('edge')) and isinstance(row.get('max_drop'),(int,float))
        and 120<row['max_drop']<=240}
    return blocked,drops

class Navigation:
    def __init__(self,mapname):
        meta=read_json(EMS/f'nav-{mapname}.json'); areas=[]
        for i in range(meta['chunks']): areas.extend(read_json(EMS/f'nav-{mapname}-{i}.json')['areas'])
        if len(areas)!=meta['area_count']: raise RuntimeError('Incomplete navigation export')
        self.areas={a['id']:a for a in areas}; self.map=mapname
        self.goal=max((a for a in areas if 0<=a['flow']<1e7),key=lambda a:a['flow'])['id']
        # The largest flow can be an optional dead end, not the safe room.
        # CHECKPOINT is the game's spawn-attribute bit 11. Finales only have
        # the starting checkpoint, so their event/escape planner stays separate.
        finales={'c1m4_atrium','c2m5_concert','c3m4_plantation','c4m5_milltown_escape','c5m5_bridge'}
        checkpoints=[a for a in areas if a.get('spawn',0)&2048 and 0<=a['flow']<1e7]
        if mapname not in finales and checkpoints:
            ending=max(checkpoints,key=lambda a:a['flow'])
            if ending['flow']>1000:self.goal=ending['id']
        self.cached_start=None;self.path=[];self.damaging=set();self.blocked=set()
        # Version the exclusion cache after changing path legality. Old failures
        # include now-open doors and the former rooftop-shortcut algorithm.
        self.recovery_file=pathlib.Path(__file__).resolve().parent/'nav-recoveries-v2.jsonl'
        rows=[json.loads(x) for x in self.recovery_file.read_text().splitlines()] if self.recovery_file.exists() else []
        live=read_json(EMS/'state.json')
        self.avoided=active_recoveries(rows,mapname,live['t']) if live['map']==mapname else set()
        self.reviewed_drops={}
        review_file=pathlib.Path(__file__).resolve().parent/'nav-route-reviews.json'
        if review_file.exists() and live['map']==mapname:
            reviewed, self.reviewed_drops=reviewed_links(read_json(review_file),live,self.areas)
            self.avoided.update(reviewed)
        ladder_file=EMS/f'ladders-{mapname}.json'
        self.ladders=read_json(ladder_file)['ladders'] if ladder_file.exists() else []
        self.ladder_edges=ladder_connections(self.ladders,self.areas);self.active_ladder=None
    def update(self,state):
        changed=False
        # Calm Witches are obstacles to avoid, not merely excluded targets.
        # Use only current observations; an angry, dead or absent Witch must
        # not leave a permanent route exclusion. This is a route preference,
        # never permission to cross otherwise illegal floor connections.
        witches=tuple(sorted((e['id'],tuple(e['p'])) for e in state.get('enemies',[])
            if e['type']==7 and e.get('rage',0)<.8 and e['health']>0 and not e.get('incap')))
        if witches!=getattr(self,'calm_witches',()):changed=True
        self.calm_witches=witches
        for a in state.get('nearby_nav',[]):
            # Finale gates can open after the initial export. Replace stale
            # flow only with fresh engine observations; never invent a route.
            observed_flow=a.get('flow');area=self.areas.get(a['id'])
            if area and type(observed_flow) in (int,float) and math.isfinite(observed_flow) and area['flow']!=observed_flow:
                area['flow']=observed_flow;changed=True
            for key,current in [('damaging',self.damaging),('blocked',self.blocked)]:
                if bool(a[key])!=(a['id'] in current):
                    changed=True
                    if a[key]: current.add(a['id'])
                    else: current.discard(a['id'])
        if changed: self.cached_start=None
    def correct_player_area(self,state):
        """Resolve an observed stacked-floor lookup error without game writes."""
        p=state['player'];current=self.areas.get(p['area'])
        if p.get('on_ladder') or abs(p.get('velocity',[0,0,0])[2])>25:return None
        missing=current is None
        if missing and (not p.get('nearest_area') or abs(p.get('velocity',[0,0,0])[2])>1
            or p['dead'] or p['pinned'] or p['incap'] or p.get('ledge')):return None
        def height_error(a):
            return max(min(a['nw'][2],a['se'][2])-p['p'][2],0,p['p'][2]-max(a['nw'][2],a['se'][2]))
        if not missing and height_error(current)<=75:return None
        candidates=[]
        for observed in state.get('nearby_nav',[]):
            a=self.areas.get(observed['id'])
            if not a or observed['blocked'] or height_error(a)>(8 if missing else 18):continue
            if missing and observed['damaging']:continue
            xy=math.hypot(*(max(a['nw'][i]-p['p'][i],0,p['p'][i]-a['se'][i]) for i in (0,1)))
            if xy<=(0 if missing else 24):candidates.append((xy+height_error(a),a['id'],observed))
        if not candidates:return None
        _,chosen,observed=min(candidates,key=lambda v:(v[0],v[1]))
        evidence={'sensor_area':p['area'],'resolved_area':chosen,'position':list(p['p']),
            'sensor_floor_error':None if missing else round(height_error(current),2),
            'basis':'Fresh observed floor contains stationary-height player with missing nearest lookup.' if missing else 'Observed adjacent nav rectangle matches standing height.'}
        p['sensor_area']=p['area'];p['area']=chosen;p['nearest_area']=True
        p['area_damaging']=bool(observed['damaging'])
        return evidence
    def route(self,start,goal=None,*,escaping=False):
        goal=goal or self.goal
        paths=self.routes_to(start,[goal],escaping=escaping)
        if goal not in paths:raise NoRouteError(f'No path from area {start} to mission goal {goal}; planner required')
        return paths[goal]
    def routes_to(self,start,goals,*,escaping=False):
        """Resolve nearby alternatives in one graph search, not one per goal."""
        if start not in self.areas: raise RuntimeError('Player outside observed navigation graph')
        targets=set(goals);remaining=set(targets);reached=set()
        queue=[(0,start)]; cost={start:0}; previous={}
        while queue and remaining:
            d,u=heapq.heappop(queue)
            if d!=cost[u]: continue
            if u in remaining:remaining.remove(u);reached.add(u)
            if not remaining:break
            a=self.areas[u]
            # An explicit non-survivor goal is a terminal destination, never
            # a shortcut through that area to one of the other candidates.
            if u!=start and not 0<=a['flow']<1e7:continue
            neighbors=set(a['adj'])|{v for (u2,v) in self.ladder_edges if u2==u}
            for v in neighbors:
                if v not in self.areas: continue
                if (u,v) in self.avoided and (u,v) not in self.ladder_edges: continue
                b=self.areas[v]
                # Exported nav includes rooftop/climb areas with no campaign
                # flow. A visible fence stall confirmed one such false shortcut.
                # Do not trust these intermediate areas for survivor travel;
                # an explicitly observed mission goal may still target one.
                if not 0<=b['flow']<1e7 and v not in targets:continue
                # Connections also describe infected climbing between floors.
                # Reject a whole destination floor above ordinary jump reach.
                # Using corner height ranges preserves sloping ramps/stairs;
                # a center-height difference alone falsely rejects those.
                minimum_rise=min(b['nw'][2],b['se'][2])-max(a['nw'][2],a['se'][2])
                if minimum_rise>65 and (u,v) not in self.ladder_edges:continue
                drop=min(a['nw'][2],a['se'][2])-max(b['nw'][2],b['se'][2])
                # Infected-only downward links can look like shortcuts for a
                # survivor. An observed280unit fall cost34health with fuel.
                # Follow stairs/ramps or actual ladders instead of those drops.
                permitted_drop=getattr(self,'reviewed_drops',{}).get((u,v),120)
                if drop>permitted_drop and (u,v) not in self.ladder_edges:continue
                if escaping:
                    # Escaping acid can require a small hop back over a curb.
                    # Keep the tighter drop/block guards and reject high jumps.
                    if minimum_rise>35 or drop>60 or v in self.blocked:continue
                # Normal walkable graph; additional height costs discourage large drops.
                dz=b['p'][2]-a['p'][2]
                weight=distance(a['p'],b['p'])+max(0,abs(dz)-60)*2
                # The motor walks in short feedback pulses on these native
                # areas. Geometric distance alone preferred thousands of
                # units of Bridge railing over a slightly longer road route.
                # Account for that control cost without making a required
                # narrow passage illegal or weakening any floor/drop guard.
                if (u,v) not in self.ladder_edges and (a.get('attr',0)|b.get('attr',0))&(4|64|32768):
                    weight*=3
                # AI nav also includes climb-up links ordinary survivor walking
                # cannot execute. Prefer the actual usable ladder or a ramp.
                separated=(a['se'][0]<b['nw'][0] or b['se'][0]<a['nw'][0] or
                           a['se'][1]<b['nw'][1] or b['se'][1]<a['nw'][1])
                if dz>45 and separated and (u,v) not in self.ladder_edges: weight+=5000
                if (u,v) in self.ladder_edges:
                    ladder,up=self.ladder_edges[(u,v)]
                    entry=ladder['bottom' if up else 'top'];leave=ladder['top' if up else 'bottom']
                    # Climbing suspends our ordinary fighting and can funnel
                    # the team into a crowded landing. Prefer a comparable
                    # native walking route; required ladders remain legal.
                    weight=distance(a['p'],entry)+distance(entry,leave)*1.2+distance(leave,b['p'])+800
                if v in self.damaging and not escaping: weight+=100000
                if v in self.blocked: weight+=10000
                for _,witch in getattr(self,'calm_witches',()):
                    clearance=segment_distance(witch,[*a['p'][:2],a['p'][2]+40],[*b['p'][:2],b['p'][2]+40])
                    if clearance<260:weight+=(260-clearance)*50
                nd=d+weight
                if nd<cost.get(v,float('inf')):
                    cost[v]=nd;previous[v]=u;heapq.heappush(queue,(nd,v))
        paths={}
        for goal in reached:
            path=[goal]
            while path[-1]!=start:path.append(previous[path[-1]])
            paths[goal]=list(reversed(path))
        return paths
    def avoid_current_edge(self,state,reason,goal_area=None,*,escaping=False):
        self.waypoint(state,goal_area,escaping=escaping)
        if len(self.path)<2: return False
        edge=tuple(self.path[:2]);self.avoided.add(edge);self.cached_start=None
        try: alternate=self.route(state['player']['area'],goal_area,escaping=escaping)
        except RuntimeError:
            self.avoided.remove(edge);return False
        with self.recovery_file.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'map':self.map,'game_t':state['t'],'edge':list(edge),'reason':reason,'alternate_length':len(alternate)})+'\n')
        return True
    def closest_area(self,point,*,below=False,reachable_from=None):
        # A large room's center can be farther away than a tiny area behind its
        # wall. Measure to the floor rectangle, not only to its center.
        def score(a):
            xy=[max(a['nw'][i]-point[i],0,point[i]-a['se'][i]) for i in (0,1)]
            z=max(min(a['nw'][2],a['se'][2])-point[2],0,point[2]-max(a['nw'][2],a['se'][2]))
            return (math.hypot(*xy)**2+z*z,distance(a['p'],point))
        candidates=[a for a in self.areas.values() if 0<=a['flow']<1e7
            and (not below or min(a['nw'][2],a['se'][2])<=point[2]+18)]
        if not candidates:raise NoRouteError('No observed floor at or below the interaction target')
        nearest=min(candidates,key=score)['id']
        if reachable_from is None:return nearest
        try:self.route(reachable_from,nearest);return nearest
        except NoRouteError:pass
        # A falling pickup can be nearest an isolated stair polygon. Choose
        # another nearby floor reachable by ordinary survivor movement.
        ordered=sorted(candidates,key=score)[:48]
        paths=self.routes_to(reachable_from,[a['id'] for a in ordered])
        for a in ordered:
            if a['id'] in paths:return a['id']
        raise NoRouteError('No nearby reachable floor for the observed pickup')
    def ladder_waypoint(self,state):
        ladder,up=self.active_ladder;p=state['player']
        normal=[(0,-1),(1,0),(0,1),(-1,0)][ladder['dir']]
        entry=ladder['bottom' if up else 'top'];leave=ladder['top' if up else 'bottom']
        landing=ladder['top_area' if up else 'bottom_area']
        landing_z=self.areas[landing]['p'][2]
        on_landing=p['p'][2]>=landing_z-12 if up else p['p'][2]<=landing_z+12
        if not p.get('on_ladder') and on_landing and flat_distance(p['p'],leave)>22:
            self.active_ladder=None;self.cached_start=None;return None
        reached=p['p'][2]>=leave[2]-6 if up else p['p'][2]<=leave[2]+6
        if reached:
            if not p.get('on_ladder') and p['area']==landing and flat_distance(p['p'],leave)>36:
                self.active_ladder=None;self.cached_start=None;return None
            point=list(self.areas[landing]['p']);phase='dismount';pitch=5
        else:
            approach=[entry[0]+normal[0]*32,entry[1]+normal[1]*32,entry[2]]
            lateral=abs((p['p'][0]-entry[0])*(-normal[1])+(p['p'][1]-entry[1])*normal[0])
            climbing=p.get('on_ladder') or abs(p['p'][2]-entry[2])>15 or (flat_distance(p['p'],approach)<14 and lateral<4)
            if climbing:
                point=[entry[0]-normal[0]*20,entry[1]-normal[1]*20,leave[2]]
                # Horizontal view stalled halfway up the tanker ladder while
                # still attached. A recorded upward-look check climbed66units
                # in0.65seconds without changing position by any other means.
                phase='climb';pitch=-50 if up else 65
            else: point=approach;phase='approach';pitch=0
        return {'p':point,'area':landing,'flow':p['flow'],'remaining_areas':len(self.path),
                'attr':0,'goal_area':self.goal,'link_type':'ladder','phase':phase,
                'pitch':pitch,'ladder_id':ladder['id'],'climbing_up':up,
                'ladder_center':list(entry),'face_yaw':math.degrees(math.atan2(-normal[1],-normal[0]))}
    def waypoint(self,state,goal_area=None,*,escaping=False):
        self.update(state)
        p=state['player']; start=p['area']
        if self.active_ladder:
            step=self.ladder_waypoint(state)
            if step: return step
        platforms=[e for e in state.get('interactables',[]) if e['type']=='func_elevator'
                   and flat_distance(e['p'],p['p'])<180 and abs(e['p'][2]-p['p'][2])<100]
        if start not in self.areas and platforms:
            center=list(platforms[0]['p']);center[2]=p['p'][2]
            return {'p':center,'area':None,'flow':p['flow'],'remaining_areas':None,
                    'attr':0,'goal_area':self.goal,'link_type':'elevator'}
        cache_key=(start,goal_area,escaping)
        if cache_key!=self.cached_start:
            try:self.path=self.route(start,goal_area,escaping=escaping)
            except RuntimeError:
                riding=[e for e in platforms if flat_distance(e['p'],p['p'])<110 and -8<=p['p'][2]-e['p'][2]<=20]
                if not riding:raise
                # A descending elevator can be assigned the lower floor's nav
                # area before its doors open. Stay on the observed platform
                # while the map advances, instead of routing back upstairs.
                center=list(riding[0]['p']);center[2]=p['p'][2]
                return {'p':center,'area':start,'flow':p['flow'],'remaining_areas':None,
                    'attr':0,'goal_area':goal_area or self.goal,'link_type':'elevator'}
            self.cached_start=cache_key
        ids=self.path
        if len(ids)>1 and tuple(ids[:2]) in self.ladder_edges:
            self.active_ladder=self.ladder_edges[tuple(ids[:2])]
            step=self.ladder_waypoint(state)
            if step:return step
            # A cached bottom area can survive one sensor tick after landing.
            # Continue onto the destination instead of returning no waypoint.
        target=self.areas[ids[min(1,len(ids)-1)]];align_portal=False
        # Aim just inside the next adjacent rectangle through its shared portal,
        # preventing center-to-center shortcuts across solid door corners.
        if len(ids)>1:
            current=self.areas[ids[0]]; nxt=target
            overlap_x=(max(current['nw'][0],nxt['nw'][0]),min(current['se'][0],nxt['se'][0]))
            overlap_y=(max(current['nw'][1],nxt['nw'][1]),min(current['se'][1],nxt['se'][1]))
            goal=list(nxt['p'])
            if overlap_x[0]<=overlap_x[1] and overlap_y[0]<=overlap_y[1]:
                portal=[sum(overlap_x)/2,sum(overlap_y)/2]
                vx,vy=nxt['p'][0]-portal[0],nxt['p'][1]-portal[1]
                scale=min(1,28/max(.001,math.hypot(vx,vy)))
                goal[:2]=[portal[0]+vx*scale,portal[1]+vy*scale]
                # Preserve clearance through a narrow shared opening. A point
                #28units inside the next rectangle can otherwise cut its corner.
                widths=[overlap_x[1]-overlap_x[0],overlap_y[1]-overlap_y[0]]
                across=0 if widths[0]<2 and widths[1]>8 else (1 if widths[1]<2 and widths[0]>8 else None)
                if across is not None:
                    along=1-across;overlap=(overlap_x,overlap_y)[along]
                    pad=min(18,(widths[along]-8)/2);low,high=overlap[0]+pad,overlap[1]-pad
                    if abs(p['p'][across]-portal[across])<160 and not low<=p['p'][along]<=high:
                        goal=list(p['p'])
                        # A nearest-area lookup can place the player against
                        # or just beyond this rectangle's opposite wall. Move
                        # inside it first; sliding along that wall toward a
                        # distant opening can repeatedly hit its solid corner.
                        span=current['se'][across]-current['nw'][across]
                        inset=min(24,max(0,(span-8)/2))
                        inner_low=current['nw'][across]+inset
                        inner_high=current['se'][across]-inset
                        # Thin doorway polygons cannot contain a whole player
                        # hull. Advancing into their center before aligning
                        # sideways can push directly against the door frame.
                        # A nearest rectangle may lie just sideways of the
                        # standing point. Align into its actual corridor
                        # before an inward step that could hit a corner wall.
                        inside_along=current['nw'][along]<=p['p'][along]<=current['se'][along]
                        needs_inset=inside_along and span>=48 and (p['p'][across]<inner_low-5 or p['p'][across]>inner_high+5)
                        # Even an exact area match can put the player hull
                        # against a counter corner. Keep the same clearance
                        # when the sensor does not label it a nearest lookup.
                        if needs_inset:
                            self.inset_alignment=(start,across)
                            goal[across]=max(inner_low,min(inner_high,p['p'][across]))
                            # The nav rectangle can extend into a doorway
                            # frame. If fresh rays confirm that the inward
                            # step hits solid geometry but lateral alignment
                            # is clear, center on the opening first.
                            lateral=list(p['p']);lateral[along]=(low+high)/2
                            def ray_to(point):
                                heading=math.degrees(math.atan2(point[1]-p['p'][1],point[0]-p['p'][0]))
                                rays=state.get('obstacles',[])
                                ray=min(rays,key=lambda r:abs(wrap(p['angles'][1]+r['angle']-heading)),default=None)
                                return ray if ray and abs(wrap(p['angles'][1]+ray['angle']-heading))<=17 else None
                            inward_ray=ray_to(goal);lateral_ray=ray_to(lateral)
                            if (inward_ray and lateral_ray and inward_ray.get('hit_type')=='worldspawn'
                                and inward_ray['fraction']*160<flat_distance(p['p'],goal)+18
                                and lateral_ray['fraction']*160>flat_distance(p['p'],lateral)+24):
                                goal=lateral;self.inset_alignment=None
                        else:
                            self.inset_alignment=None
                            goal[along]=(low+high)/2
                        align_portal=True
                    else:
                        # A very narrow opening has almost no lateral margin.
                        # Keeping an already-valid edge coordinate can still
                        # scrape the open door leaf with the player's hull.
                        goal[along]=(low+high)/2 if widths[along]<48 else max(low,min(high,p['p'][along]))
                        direction=nxt['p'][across]-current['p'][across]
                        goal[across]=portal[across]+(24 if direction>0 else -24)
        else: goal=list(target['p'])
        link_type='walk'
        elevators=[e for e in state.get('interactables',[]) if e['type']=='func_elevator'
                   and flat_distance(e['p'],p['p'])<300 and abs(e['p'][2]-p['p'][2])<100]
        if not goal_area and abs(goal[2]-p['p'][2])>300 and elevators:
            # An elevator connection is not a walkable segment through a wall.
            # Board the observed platform and let a normal Use action start it.
            goal=list(elevators[0]['p']);goal[2]=p['p'][2];link_type='elevator'
        return {'p':goal,'area':target['id'],'flow':target['flow'],'remaining_areas':len(ids),
                'attr':target['attr'],'goal_area':goal_area or self.goal,'link_type':link_type,'align_portal':align_portal}
