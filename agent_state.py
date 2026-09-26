"""Privacy-allowlisted perception and evidence memory for a general game agent."""
import collections
import math
from game_knowledge import ENEMIES,firearm,mission
from navigation import distance,flat_distance,look,wrap

def friendly_blocked(state,target):
    eye=state['player']['eye'];ray=[b-a for a,b in zip(eye,target)];rr=sum(x*x for x in ray)
    if rr<1:return True
    for b in state.get('teammates',[]):
        if b['dead']:continue
        center=[b['p'][0],b['p'][1],b['p'][2]+38];v=[b-a for a,b in zip(eye,center)]
        t=sum(a*b for a,b in zip(v,ray))/rr
        if 0<t<1 and math.sqrt(sum((v[i]-t*ray[i])**2 for i in range(3)))<28:return True
    return False

def engageable(state,enemy):
    # Don't waste moving rifle fire on distant common infected. Special
    # infected remain available, and commons become targets as they close.
    return enemy['type']!=0 or distance(state['player']['eye'],enemy['p'])<=700

def is_riot(enemy):
    return enemy['type']==0 and 'riot' in enemy.get('model','').lower()

def riot_front(state,enemy):
    if not is_riot(enemy):return False
    forward=enemy.get('forward')
    if not forward or len(forward)!=3:return True
    direction=[state['player']['p'][i]-enemy['p'][i] for i in (0,1)]
    return sum(direction[i]*forward[i] for i in (0,1))>=0

def active_ammo(state):
    p=state['player']
    found=next((v for v in p.get('weapons',{}).values() if v['type']==p['weapon']),{})
    maximum=found.get('max_clip',-1)
    # L4D2 pistols have unlimited reloads; GetAmmoCount can still return zero.
    # Treating that counter as finite caused a swap to an empty primary.
    infinite=p['weapon'] in ('weapon_pistol','weapon_pistol_magnum')
    return {'clip':p['clip'],'reserve':-1 if infinite else found.get('reserve',-1),'infinite_reserve':infinite,'max_clip':maximum,
            'empty':p['clip']==0,'nearly_empty':0<=p['clip']<=max(1,maximum*.2),
            'reloading':found.get('reloading',False),'ready':max(p.get('next_attack',0),found.get('next_attack',0))<=state['t']}

def needs_ammo_resupply(player):
    primary=player.get('weapons',{}).get('slot0',{})
    minimum=24 if 'shotgun' in primary.get('type','') else 80
    return (firearm(primary.get('type','')) and primary.get('reserve',-1)>=0
        and primary['reserve']<max(minimum,4*primary.get('max_clip',0)))

def critical_ammo_resupply(player):
    primary=player.get('weapons',{}).get('slot0',{})
    minimum=12 if 'shotgun' in primary.get('type','') else 80
    return (needs_ammo_resupply(player)
        and primary.get('reserve',0)+primary.get('clip',0)<max(minimum,2*primary.get('max_clip',0)))

def perceive(state,memory,waypoint):
    p=state['player'];ammo=active_ammo(state);enemies=[]
    for e in state.get('enemies',[]):
        d=distance(p['eye'],e['p']);angle=look(p['eye'],e['p'])
        calm=e['type']==7 and e.get('rage',0)<.8
        pinned_target=any(b.get('pinned') and flat_distance(b['p'],e['p'])<170 for b in state.get('teammates',[]))
        enemies.append({'id':e['id'],'kind':'riot infected' if is_riot(e) else ENEMIES.get(e['type'],'infected'),'type':e['type'],
            'frontal_armor':riot_front(state,e),
            'distance':round(d),'range':'touching' if d<160 else 'near' if d<500 else 'far',
            'health':e['health'],'in_front':abs(wrap(angle[1]-p['angles'][1]))<55,
            'aim_error':round(abs(wrap(angle[1]-p['angles'][1])),1),'calm_witch':calm,
            'pinned_teammate_nearby':pinned_target,'friendly_blocks_shot':friendly_blocked(state,e['p'])})
    enemies.sort(key=lambda e:(e['calm_witch'],not e['pinned_teammate_nearby'],e['range']!='touching',e['type']==0,e['distance']))
    teammates=[{'id':b['id'],'distance':round(distance(p['p'],b['p'])),'health':b['health'],
                'dead':b['dead'],'incapacitated':b['incap'],'pinned':b['pinned'],'ledge':b.get('ledge',False),
                'has_medkit':b.get('has_medkit',False)} for b in state.get('teammates',[])]
    return {'game':'Left 4 Dead 2','mission':mission(state),
        'self':{'health':p['health'],'temporary_health':round(p.get('temp_health',0)),
            'critical_health':p['health']+p.get('temp_health',0)<30,'slow_from_injury':p['health']+p.get('temp_health',0)<40,
            'weapon':p['weapon'],'holding_firearm':firearm(p['weapon']),'ammo':ammo,
            'inventory':dict(p['inventory']),'pinned':p['pinned'],'incapacitated':p['incap'],
            'on_ladder':p.get('on_ladder',False),'in_hazard':p['on_fire'] or p['area_damaging'],
            'recent_damage':memory.recent_damage,'recent_verified_shots':memory.recent_shots},
        'threats':enemies[:8],'team':teammates,'route':dict({k:waypoint.get(k) for k in ('link_type','phase','remaining_areas','ladder_id')},goal=memory.navigation_goal),
        'combat_facts':{'active_enemy_visible':any(not (e['type']==7 and e.get('rage',0)<.8) and engageable(state,e) for e in state.get('enemies',[])),
            'enemy_touching':any(not e['calm_witch'] and e['range']=='touching' for e in enemies),
            'loaded_primary_available':bool(firearm(p.get('weapons',{}).get('slot0',{}).get('type',''))
                and p['weapons']['slot0'].get('clip',0)>0)},
        'recent_outcomes':list(memory.outcomes)[-5:],'movement_problem':memory.movement_problem,
        'public_planner_note':memory.planner_note[:900],
        'visual_observations':memory.visual_facts,
        'unfinished_task':memory.current_task}

class EvidenceMemory:
    def __init__(self):
        self.history=collections.deque();self.outcomes=collections.deque(maxlen=40)
        self.cooldowns={};self.failures=collections.Counter();self.previous=None
        self.recent_damage=0;self.recent_shots=0;self.movement_problem=None;self.planner_note=''
        self.remembered_items={};self.completed=set()
        self.visual_facts=None;self.current_task=None;self.navigation_goal=None
        self.tank_damage_history=collections.deque();self.recent_tank_damage=0
    def update(self,state):
        p=state['player'];t=state['t'];previous=self.previous
        if previous and (previous['map']!=state['map'] or previous['t']>t):
            self.history.clear();self.cooldowns.clear();self.remembered_items.clear();self.completed.clear()
            self.visual_facts=None;self.current_task=None;self.planner_note='';self.navigation_goal=None
            self.tank_damage_history.clear();previous=None
        old_tanks={e['id']:e['health'] for e in previous.get('enemies',[]) if e['type']==8} if previous else {}
        damage=sum(max(0,old_tanks.get(e['id'],e['health'])-e['health']) for e in state.get('enemies',[]) if e['type']==8)
        if damage:self.tank_damage_history.append((t,damage))
        while self.tank_damage_history and t-self.tank_damage_history[0][0]>4:self.tank_damage_history.popleft()
        self.recent_tank_damage=sum(row[1] for row in self.tank_damage_history)
        inventory=tuple(sorted(p['inventory'].items()))
        self.history.append((t,list(p['p']),p['health']+p.get('temp_health',0),state.get('metrics',{}).get('shots',0),inventory))
        while self.history and t-self.history[0][0]>32:self.history.popleft()
        short=[r for r in self.history if t-r[0]<5]
        self.recent_damage=max(0,max((r[2] for r in short),default=0)-(p['health']+p.get('temp_health',0)))
        self.recent_shots=max(0,short[-1][3]-short[0][3]) if short else 0
        self.movement_problem=None
        if len(self.history)>2 and not (p['pinned'] or p['incap'] or p.get('immobilized')):
            still=[r for r in self.history if t-r[0]<=10]
            cycle=[r for r in self.history if t-r[0]<=26]
            if still and t-still[0][0]>9 and max(distance(p['p'],r[1]) for r in still)<35:
                self.movement_problem='standing_still'
            elif cycle and t-cycle[0][0]>24 and max(distance(p['p'],r[1]) for r in cycle)<250:
                self.movement_problem='cycling_in_small_area'
        for item in state.get('items',[])+state.get('interactables',[]):
            if not item.get('owned'):self.remembered_items[item['id']]={'item':dict(item),'seen_t':t,'map':state['map']}
        self.remembered_items={k:v for k,v in self.remembered_items.items() if t-v['seen_t']<120 and v['map']==state['map']}
        self.previous=state
    def allowed(self,key,t):return t>=self.cooldowns.get(key,-1)
    def outcome(self,key,status,t,evidence,cooldown=0):
        self.outcomes.append({'task':key,'status':status,'evidence':evidence[:160],'game_t':round(t,2)})
        if status in ('failed','needs_confirmation'):self.failures[key]+=1
        if cooldown:self.cooldowns[key]=t+cooldown
        if status=='verified':self.completed.add(key);self.failures.pop(key,None)
    def reset_motion(self):self.history.clear();self.movement_problem=None
