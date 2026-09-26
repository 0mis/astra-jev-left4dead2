"""Persistent, map-scoped public plans and observed mission landmarks.

This supplies goals to ordinary navigation. It never invokes entity inputs,
changes game variables, teleports, or treats an issued action as completion.
"""
import json
import pathlib
from game_knowledge import role_for,mission,CARRY_ITEMS,consumed_control,ferry_stage,elevator_stage
from navigation import distance,NoRouteError
from agent_state import needs_ammo_resupply,critical_ammo_resupply

ROOT=pathlib.Path(__file__).resolve().parent
EVENT_SHUTOFFS={'shut off mall alarm','stop carousel','stop coaster alarm','stop evacuation alarm'}
EVENT_STARTS={'open plane exit':'plane_exit_opened','lower bridge':'shanty_bridge_lowering','call elevator':'sugarmill_elevator_called','ride elevator':'sugarmill_elevator_departed','stop evacuation alarm':'park_alarm_stopped','clear route with tractor':'quarter_tractor_started'}
EVENT_APPROACHES=EVENT_SHUTOFFS|{'open plane exit','lower bridge','clear route with tractor'}
RESCUE_ZONES={'c3m4_plantation':'escape_boat_trigger','c4m5_milltown_escape':'trigger_boat','c5m5_bridge':'trigger_heli'}

def observed_rescue_frontier(s,nav,control):
    """Approach a live rescue over reachable floors, refreshing opened gates."""
    nav.update(s);p=s['player'];target=control['p']
    floors=[a for a in nav.areas.values() if 0<=a.get('flow',-9999)<1e7]
    # Include the actual destination and nearby frontiers. The latter let
    # live observations extend the route past a newly opened gate, without
    # authorizing unknown infected-only links from the initial export.
    ordered=sorted(floors,key=lambda a:distance(a['p'],target))
    candidates={a['id']:a for a in ordered[:96]}
    candidates.update({a['id']:a for a in [a for a in ordered if distance(a['p'],p['p'])<1200][:96]})
    paths=nav.routes_to(p['area'],candidates)
    if not paths:raise NoRouteError('No observed reachable floor toward the enabled rescue')
    return min(paths,key=lambda i:distance(nav.areas[i]['p'],target))

class MissionPlanner:
    def __init__(self,nav,memory):
        self.nav=nav;self.memory=memory;self.exit_goal=nav.goal;self.phase=None
        self.directive_version=None;self.landmarks={};self.intent=None
        self.landmark_path=ROOT/'mission-landmarks.json';self.loaded_t=None;self.loaded_round=None
        self.fuel=None
        if nav.map=='c1m4_atrium':
            from fuel_planner import FuelPlanner
            self.fuel=FuelPlanner(ROOT,nav)
        if self.landmark_path.exists():
            saved=json.loads(self.landmark_path.read_text(encoding='utf-8'))
            if saved.get('map')==nav.map:
                self.landmarks=saved.get('landmarks',{});self.loaded_t=saved.get('game_t')
                self.loaded_round=saved.get('round_id')
    def update(self,s):
        p=s['player'];t=s['t'];self.phase=mission(s)['phase'];self.nav.goal=self.exit_goal
        self.memory.planner_note=mission(s)['objective']
        path=ROOT/'planner-directive.json'
        try:directive=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):directive=None
        if not valid_directive(directive,s,self.nav):directive=None
        self.memory.requested_ammo_refill=(directive.get('refill_ammo_id') if directive and self.phase!='escape' else None)
        navigation_review=bool(directive and directive.get('navigation_review') is True)
        if self.loaded_t is not None:
            if self.loaded_t>t or self.loaded_round!=s.get('round_id'):self.landmarks={}
            self.loaded_t=None
        before=json.dumps(self.landmarks,sort_keys=True)
        for record in self.memory.remembered_items.values():
            item=record['item'];role=role_for(s,item)
            if role in ('collect cola','deliver cola','collect fuel','pour fuel'):
                self.landmarks[role]=item
            elif item['type']=='weapon_ammo_spawn':self.landmarks['refill ammunition']=item
            elif role in EVENT_SHUTOFFS|EVENT_STARTS.keys() and not (item.get('locked') or item.get('disabled')):
                key='event:'+role;previous=self.landmarks.get(key,{})
                self.landmarks[key]=dict(item,first_seen_game_t=previous.get('first_seen_game_t',t),
                    observed_use_game_t=previous.get('observed_use_game_t'),
                    removed_since=previous.get('removed_since'),consumed_game_t=previous.get('consumed_game_t'))
        used=s.get('metrics',{}).get('last_use')
        for key,item in self.landmarks.items():
            if key.startswith('event:') and used and used['target']==item['id'] and used['t']>=item['first_seen_game_t']:
                item['observed_use_game_t']=used['t']
            if key.startswith('event:') and consumed_control(s,item):
                if item.get('removed_since') is None:item['removed_since']=t
                if t-item['removed_since']>=.3 and item.get('consumed_game_t') is None:
                    item['consumed_game_t']=t
                    item['consumed_evidence']='Known single-use control absent from complete nearby observations after activation.'
            elif key.startswith('event:'):item['removed_since']=None
        if json.dumps(self.landmarks,sort_keys=True)!=before:
            tmp=self.landmark_path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'map':s['map'],'round_id':s.get('round_id'),'game_t':t,'landmarks':self.landmarks},indent=2),encoding='utf-8');tmp.replace(self.landmark_path)
        # Derive event timing only from Use on a previously observed named
        # control in this round. Merely seeing or losing sight of it is not
        # an activation. The persisted landmark survives controller handoffs.
        s['observed_mission_events']=[{'kind':EVENT_STARTS[role_for(s,item)],'t':item['observed_use_game_t']}
            for key,item in self.landmarks.items() if key.startswith('event:')
            and role_for(s,item) in EVENT_STARTS
            and isinstance(item.get('observed_use_game_t'),(int,float))
            and item['first_seen_game_t']<=item['observed_use_game_t']<=t]
        self.phase=mission(s)['phase']
        if self.phase=='defend' and s['map']=='c3m2_swamp':
            self.memory.planner_note='The plane exit was activated. Stay with the bots and defend for a brief interval; heal or boost when possible. Move away from fire, acid or a close Tank as needed. Resume the chapter route after this bounded defense interval.'
        elif self.phase=='defend' and s['map']=='c3m3_shantytown':
            self.memory.planner_note='The bridge button was activated and the bridge is lowering. Stay with the team and defend during its brief movement delay. Heal or evade immediate hazards if necessary, then resume the ordinary crossing route.'
        elif self.phase=='defend' and s['map'] in ('c5m2_park','c5m4_quarter'):
            self.memory.planner_note='The required control was activated. Defend with the team while the exit doors finish opening or the tractor clears the route. Heal, refill critical ammunition or evade hazards as needed, then resume the route after this bounded movement interval.'
        # Mission items remain remembered beyond the short perception cache.
        # Targets must still be reacquired before a pickup/use is attempted.
        self.intent=None;self.memory.navigation_goal=None;self.memory.navigation_review=False
        carry_complete=mission(s)['carry_complete']
        fuel_intent=self.fuel.update(s,self.memory) if self.fuel and not carry_complete else None
        if p['weapon'] in CARRY_ITEMS and not carry_complete:
            role='deliver cola' if p['weapon']=='weapon_cola_bottles' else 'pour fuel'
            if getattr(self.memory,'staging_target',None):self.intent=self.memory.staging_target
            elif role in self.landmarks:self.intent=self.landmarks[role]
        elif not carry_complete and s['map']=='c1m2_streets' and any(e['kind']=='explain_store_item' for e in s.get('metrics',{}).get('hints',[])):
            if 'collect cola' in self.landmarks:self.intent=self.landmarks['collect cola']
        elif fuel_intent:self.intent=fuel_intent
        # Required controls can be outside immediate Use range. Approach
        # observed event starts as well as alarm shutoffs; the final route
        # can remain blocked until they are operated. Only shutoffs outrank
        # critical resupply. Use still requires live reacquisition.
        event_controls=[item for key,item in self.landmarks.items() if key.startswith('event:')
            and role_for(s,item) in EVENT_APPROACHES
            and item.get('observed_use_game_t') is None and item.get('consumed_game_t') is None
            and 0<=t-item['first_seen_game_t']<=600]
        if event_controls and p['weapon'] not in CARRY_ITEMS and not carry_complete:
            self.intent=min(event_controls,key=lambda item:distance(p['p'],item['p']))
        urgent_event=self.intent is not None and role_for(s,self.intent) in EVENT_SHUTOFFS
        ammo=self.landmarks.get('refill ammunition')
        critical_ammo=critical_ammo_resupply(p)
        evacuation_refill=(self.phase=='escape' and critical_ammo and ammo
            and distance(p['p'],ammo['p'])<700 and abs(p['p'][2]-ammo['p'][2])<100
            and any(i['id']==ammo['id'] and i.get('visible') is not False and not i.get('owned')
                for i in s.get('items',[])))
        if (ammo and (self.phase!='escape' or evacuation_refill) and not urgent_event and not carry_complete and p['weapon'] not in CARRY_ITEMS and needs_ammo_resupply(p)
            and (not navigation_review or critical_ammo)
            and distance(p['p'],ammo['p'])<(2500 if critical_ammo else 1100)
            and (critical_ammo or abs(p['p'][2]-ammo['p'][2])<100)
            and self.memory.allowed('loot_'+str(ammo['id']),t)):
            self.intent=ammo
        # A checkpoint can span several floors. Approach its actual open
        # entrance on the native graph before starting the short Use skill;
        # the deepest checkpoint corner need not be beside that entrance.
        checkpoint_doors=[e for e in s.get('interactables',[])
            if e.get('type')=='prop_door_rotating_checkpoint' and e.get('door_state')==2
            and not e.get('disabled') and distance(e['p'],self.nav.areas[self.exit_goal]['p'])<650]
        if (self.phase=='safe_room' and self.nav.areas.get(p['area'],{}).get('spawn',0)&2048
            and checkpoint_doors):
            self.intent=min(checkpoint_doors,key=lambda e:distance(e['p'],p['p']))
        if self.intent:
            goal_point=list(self.intent['p'])
            if p['weapon'] in CARRY_ITEMS and not self.intent.get('stage'):
                # Use targets are at hand/eye height. Routing feet to their
                # origin put us on the car roof, beyond pouring reach.
                goal_point[2]-=max(40,min(64,p['eye'][2]-p['p'][2]))
            # A freshly dropped pickup may still be at hand height. Its
            # nearest 3D rectangle can be an inaccessible ledge above it;
            # approach on a floor beneath the observed object instead.
            pickup=self.intent.get('type','').startswith('weapon_')
            self.nav.goal=self.nav.closest_area(goal_point,below=pickup,reachable_from=p['area'] if pickup else None)
            kind=self.intent.get('type','')
            if urgent_event:
                purpose='Reach and operate the observed '+role_for(s,self.intent)+' control before leaving the active event for distant supplies.'
            elif role_for(s,self.intent) in EVENT_STARTS:
                purpose='Approach the observed required '+role_for(s,self.intent)+' control and reacquire it before using it.'
            elif kind=='weapon_ammo_spawn':
                purpose='Reach the remembered ammunition pile and refill the rifle.'
            elif kind=='prop_door_rotating_checkpoint':
                purpose='Approach the actual open checkpoint entrance, then close it when all living teammates are inside.'
            elif self.intent.get('stage'):
                purpose='Carry fuel to the reviewed throwing position.'
            elif p['weapon'] in CARRY_ITEMS:
                purpose='Carry the held mission object to its delivery target.'
            elif kind=='search':
                purpose='Search this map location for fuel; no live can is confirmed there.'
            else:
                purpose='Reacquire the remembered '+(role_for(s,self.intent) or kind or 'mission object')+'.'
            self.memory.navigation_goal={'purpose':purpose,'critical_supply':kind=='weapon_ammo_spawn' and critical_ammo,
                'urgent_event':urgent_event,
                'distance':round(distance(p['p'],self.intent['p'])),'requires_reacquisition':p['weapon'] not in CARRY_ITEMS}
            self.memory.planner_note='Reach the remembered mission landmark and reacquire the object before interacting. Carrying an object may require temporarily dropping it to defend the team.'
            if self.intent.get('stage'):
                self.memory.planner_note='Carry the fuel to the reviewed balcony standing point. Stop there and throw over the railing toward the reviewed clear floor. A released can is not a verified landing or a fuel pour.'
        if directive:
            if valid_directive(directive,s,self.nav):
                self.memory.planner_note=directive['public_instruction'][:900]
                self.memory.navigation_review=bool(directive.get('navigation_review') is True
                    and not carry_complete and p['weapon'] not in CARRY_ITEMS)
                if (directive.get('goal_area') is not None and not carry_complete
                    and (self.intent is None or directive.get('override_mission_goal') is True)):
                    self.nav.goal=directive['goal_area']
                    self.memory.navigation_goal=None
                self.directive_version=directive['version']
        if carry_complete:
            self.memory.planner_note='The game confirmed the carry objective is complete. Proceed along the now-open route to the chapter exit; do not retrieve the old mission item again.'
            if self.fuel:
                # Installed Valve map: trigger_escape origin, hammerid11073.
                # Only route there after an actual engine escape-ready event.
                self.nav.goal=self.nav.closest_area([-4753.98,-3494,48])
                self.memory.navigation_goal={'purpose':'Reach the fueled car with all living teammates for the escape.','critical_supply':False,'requires_reacquisition':False}
                self.memory.planner_note='The game confirmed the fueled car is ready. Gather with all living teammates at the race car escape area and defend until the actual escape sequence begins. Do not collect more fuel.'
        transport=ferry_stage(s)
        if transport:
            self.memory.navigation_review=True
            self.memory.navigation_goal=None
            self.memory.planner_note={
                'arriving':'The ferry is still approaching. Stay on the dock and fight while it arrives.',
                'boarding':'The ferry has reached the dock. Walk onto the ferry center NOW using the current route. Boarding is the required next action.',
                'gathering':'You are aboard the ferry. Hold this position briefly so all living teammates can board and unlock its button.',
                'operate':'The entire team has boarded and the ferry button is unlocked. Approach and Use the boat button now to depart.',
                'riding':'The ferry is moving across the river. Stay aboard and fight; resume walking after it reaches the far bank.'}[transport]
            if transport=='boarding':
                self.nav.goal=self.nav.closest_area([-5248,6064,4])
                self.memory.navigation_goal={'purpose':'The ferry is at the dock. Walk onto its center now.','critical_supply':True,'requires_reacquisition':False}
            elif transport=='operate':
                button=next(e for e in s['interactables'] if e.get('name')=='ferry_tram_button')
                self.nav.goal=self.nav.closest_area([button['p'][0],button['p'][1]+35,4])
        if s['map']=='c4m3_sugarmill_b' and not any(e['kind']=='sugarmill_elevator_departed' for e in s['observed_mission_events']):
            # Installed map's bottom landing is the required intermediate
            # destination. Its vertical elevator link is not a walking edge.
            self.nav.goal=self.nav.closest_area([-1478,-9551,141])
            self.memory.planner_note='Return to the elevator bottom landing, avoiding calm Witches and gathering useful supplies. Board the actual platform with the team and Use its unlocked inside button.'
            self.memory.navigation_goal=None
        lift_stage=elevator_stage(s)
        if lift_stage:
            self.memory.navigation_review=True;self.memory.navigation_goal=None
            self.memory.planner_note={
                'lift_arriving':'The elevator was called and is still rising. Defend at the landing, heal if needed, and wait for the actual platform to stop at this floor. Do not repeat the call.',
                'lift_boarding':'The actual elevator has stopped at the boarding floor. Walk onto its center so the bots can join you.',
                'lift_gathering':'Stay on the elevator center and defend while the living team boards. Its inside button unlocks only when the team is aboard.',
                'lift_operate':'The elevator button is unlocked. Use the inside button to begin the ride.',
                'lift_riding':'Stay on the actual elevator while it moves. Resume the chapter route only after it stops at the destination floor.'}[lift_stage]
            if lift_stage in ('lift_boarding','lift_operate'):
                lift=next(e for e in s['interactables'] if e.get('name')=='elevator')
                point=list(lift['p']);point[2]+=15
                self.nav.goal=self.nav.closest_area(point)
                self.memory.navigation_goal={'purpose':self.memory.planner_note,'critical_supply':False,'requires_reacquisition':False}
        rescue=next((e for e in s.get('interactables',[]) if e.get('name')==RESCUE_ZONES.get(s['map'])
            and e.get('name') is not None and e.get('disabled') is False),None)
        if s['map']=='c5m5_bridge' and not mission(s)['finale_started']:
            # The radio first creates the actual finale switch. The full
            # bridge route is closed until those ordinary interactions occur.
            controls=[e for e in s.get('interactables',[]) if e.get('name') in ('radio_fake_button','finale')
                and not e.get('disabled')]
            if controls:
                control=min(controls,key=lambda e:e.get('name')!='radio_fake_button')
                self.bridge_control_goal=self.nav.closest_area(control['p'])
            if getattr(self,'bridge_control_goal',None) is not None:
                self.nav.goal=self.bridge_control_goal
                self.memory.navigation_goal={'purpose':'Reach the bridge radio, answer it, then operate the revealed finale switch when ready.',
                    'critical_supply':False,'requires_reacquisition':True}
        bridge_ready=s['map']!='c5m5_bridge' or any(e['kind']=='finale_vehicle_ready' for e in s.get('events',[]))
        if (s['map']=='c5m5_bridge' and mission(s)['finale_started'] and self.phase!='defend' and not bridge_ready
            and not (self.memory.navigation_goal or {}).get('critical_supply')):
            # The moving span begins with invalid flow. Advance only across
            # native survivor floors as their live flow becomes available.
            if t>=getattr(self,'bridge_refresh_t',-1):
                self.bridge_frontier=observed_rescue_frontier(s,self.nav,self.nav.areas[self.exit_goal])
                self.bridge_refresh_t=t+2
            self.nav.goal=self.bridge_frontier
            self.memory.navigation_goal={'purpose':'Cross the bridge on the currently open survivor route, fighting while moving toward rescue.',
                'critical_supply':False,'requires_reacquisition':False}
        if (self.phase=='escape' and bridge_ready and rescue and p['area'] in self.nav.areas
            and not (self.memory.navigation_goal or {}).get('critical_supply')):
            if t>=getattr(self,'rescue_refresh_t',-1):
                self.rescue_goal=observed_rescue_frontier(s,self.nav,rescue);self.rescue_refresh_t=t+2
            self.nav.goal=self.rescue_goal
            self.memory.navigation_review=True
            self.memory.navigation_goal={'purpose':'The rescue vehicle is actually ready. Follow the opened escape route to board with the team.',
                'critical_supply':False,'rescue':True,'requires_reacquisition':False}
            self.memory.planner_note='The observed rescue escape zone is enabled. Evacuate with the living team now, fighting while moving. The route advances as newly opened ground is observed. Boarding requires the actual ending event; arrival near the vehicle alone is not victory.'
        # After an interrupted chapter restart the survivor may have only a
        # pistol. A bounded Astra-reviewed search can return to a supply table,
        # but cannot manufacture a pickup or persist after a primary is owned.
        if (directive and directive.get('missing_primary_search') is True
            and directive.get('goal_area') is not None and 'slot0' not in p['inventory']
            and self.phase!='escape' and p['weapon'] not in CARRY_ITEMS
            and directive['expires_game_t']-directive['created_game_t']<=120
            and distance(p['p'],self.nav.areas[directive['goal_area']]['p'])<=2000):
            self.nav.goal=directive['goal_area'];self.memory.navigation_review=False
            self.memory.navigation_goal={'purpose':directive['public_instruction'][:900],
                'critical_supply':True,'requires_reacquisition':True}
        # A different goal invalidates the cached route even on the same area.
        if getattr(self,'previous_goal',None)!=self.nav.goal:self.nav.cached_start=None
        self.previous_goal=self.nav.goal

def valid_directive(d,s,nav):
    return (isinstance(d,dict) and d.get('map')==s['map']
        and d.get('round_id')==s.get('round_id')
        and isinstance(d.get('version'),str) and isinstance(d.get('public_instruction'),str)
        and isinstance(d.get('created_game_t'),(int,float)) and isinstance(d.get('expires_game_t'),(int,float))
        and d['created_game_t']<=s['t']<=d['expires_game_t']
        and 0<d['expires_game_t']-d['created_game_t']<=600+1e-6
        and (d.get('goal_area') is None or d['goal_area'] in nav.areas))
