"""Reusable feedback skills. All effects are ordinary bounded player inputs."""
import math
from agent_policy import Task
from agent_state import active_ammo,friendly_blocked,engageable,riot_front,is_riot
from game_knowledge import CARRY_ITEMS,firearm,mission,consumed_control
from navigation import distance,flat_distance,look,wrap,NoRouteError

class NoEscapeRoute(RuntimeError):pass

class CombatFocus:
    """Finish a short aim movement while continuously checking its target."""
    def __init__(self):self.target=None;self.until=0
    def select(self,s,decision):
        eye=s['player']['eye'];t=s['t']
        enemies={e['id']:e for e in s['enemies']
            if e['health']>0 and engageable(s,e) and not (e['type']==7 and e.get('rage',0)<.8)}
        wanted=enemies.get(decision['target_id']) if decision else None
        combat=decision['combat'] if decision else 'hold'
        def priority(e):
            d=distance(eye,e['p'])
            if e['type'] in (1,3,5,6) and any(b.get('pinned') and not b['dead'] and flat_distance(b['p'],e['p'])<170 for b in s['teammates']):return 0
            if e['type']==8 and d<850:return 1
            if e['type'] in (1,3,5,6) and d<450:return 2
            return 3 if d<180 else 4
        held=enemies.get(self.target)
        explicit_hold=decision and decision['target_id'] is None
        if (held and t<self.until and not explicit_hold
            and not friendly_blocked(s,held['p'])
            and (wanted is None or priority(wanted)>=priority(held))):
            if wanted is None or wanted['id']!=held['id']:combat='fire'
            wanted=held
        # A fresh Hunter crossed332->169units while an asynchronous common-
        # infected choice was still valid, then pinned the player. React to
        # observed close pinning specials before ordinary targets; keep the
        # route/hazard action independent and retain friendly-fire checks.
        pinning=[e for e in enemies.values() if e['type'] in (1,3,5,6)
                 and distance(eye,e['p'])<450 and not friendly_blocked(s,e['p'])]
        if pinning:
            urgent=min(pinning,key=lambda e:(priority(e),distance(eye,e['p'])))
            if wanted is None or priority(urgent)<priority(wanted):
                wanted=urgent
                combat='shove' if urgent['type'] in (1,3,5) and distance(eye,urgent['p'])<140 else 'fire'
        if wanted is None and not explicit_hold:
            # Network gaps and vanished targets need not suppress immediate
            # defense. This uses only currently observed touching attackers.
            urgent=[e for e in enemies.values() if distance(eye,e['p'])<160 and not friendly_blocked(s,e['p'])]
            if urgent:wanted=min(urgent,key=lambda e:(priority(e),distance(eye,e['p'])));combat='fire'
        ident=wanted['id'] if wanted else None
        if ident!=self.target:self.target=ident;self.until=t+.9
        return wanted,combat

def movement_keys(position,point,camera_yaw,stop=12):
    if flat_distance(position,point)<=stop:return []
    heading=math.degrees(math.atan2(point[1]-position[1],point[0]-position[0]))
    relative=wrap(heading-camera_yaw);keys=[]
    if abs(relative)<67.5:keys.append('w')
    elif abs(relative)>112.5:keys.append('s')
    if 22.5<relative<157.5:keys.append('a')
    elif -157.5<relative<-22.5:keys.append('d')
    return keys

def retreat_tanks(s,already_retreating=False):
    # Avoid dropping fuel, retreating a step, and immediately picking it up
    # again while the same live Tank remains nearby. A wider release radius
    # keeps the existing retreat stable without remembering unseen enemies.
    radius=850 if already_retreating else 550
    return [e for e in s['enemies'] if e['type']==8 and distance(s['player']['eye'],e['p'])<radius]

class SkillMotor:
    def __init__(self,nav,memory):
        self.nav=nav;self.memory=memory;self.task=None;self.started=0;self.baseline=None
        self.last_jump=-100;self.last_shove=-100;self.last_semi_press=-100;self.last_use=-100
        self.last_look_speed=[0.,0.];self.last_health={};self.blocked_fire_since=None
        self.escape_goal=None
        self.remembered_tanks=[];self.tank_retreat_until=-100
        self.combat_focus=CombatFocus()
    def _start(self,task,s):
        self.task=task;self.started=s['t'];self.baseline=s
        self.stage_fired_t=None
        self.stage_arrived=False;self.stage_ready_since=None
    def _finish(self,s,status,evidence,cooldown=3):
        self.memory.outcome(self.task.key,status,s['t'],evidence,cooldown)
        self.task=None;self.baseline=None
        self.memory.current_task=None
    def _entities(self,s):return {x['id']:x for x in s['items']+s.get('interactables',[])+s['teammates']}
    def _verify(self,s):
        task=self.task
        if task and task.key=='await_aid' and s['t']-self.started>=task.timeout:
            self._finish(s,'failed','Bot healing did not occur during the bounded wait; continue to supplies before trying another aid wait.',120)
            return
        if not task or task.kind in ('route','hold','escape','evade'):return
        p=s['player'];old=self.baseline['player'];entities=self._entities(s)
        current=entities.get(task.target['id']) if task.target else None
        kind=task.kind
        if kind=='stage' and p['weapon']!='weapon_gascan':
            if self.stage_fired_t is None:
                self._finish(s,'interrupted','Fuel was lost before the intended throw.',1)
            elif s['t']-self.stage_fired_t>=.8:
                for w in old.get('weapons',{}).values():
                    if w.get('type')=='weapon_gascan' and w.get('id') is not None:
                        # Let the projectile settle instead of chasing the
                        # same can while it is still passing the balcony.
                        self.memory.cooldowns['carry_'+str(w['id'])]=s['t']+8
                        if task.target.get('landing_p') is not None:
                            if not hasattr(self.memory,'released_fuel'):self.memory.released_fuel={}
                            self.memory.released_fuel[str(w['id'])]={'t':s['t'],
                                'search_p':list(task.target['landing_p']),'status':'released'}
                self._finish(s,'input_accepted','Fuel released by the ordinary throw button. Landing and delivery remain unverified.',3)
            return
        if kind=='deliver' and old['weapon'] not in CARRY_ITEMS:
            self._finish(s,'interrupted','Delivery decision arrived after the mission object was lost.',1);return
        if kind in ('loot','carry'):
            changed=p['inventory']!=old['inventory']
            ammo_gained=sum(max(0,w.get('reserve',0)) for w in p.get('weapons',{}).values())>sum(max(0,w.get('reserve',0)) for w in old.get('weapons',{}).values())
            acquired=(old['weapon'] not in CARRY_ITEMS and p['weapon'] in CARRY_ITEMS) if kind=='carry' else (changed or ammo_gained)
            if acquired:
                if kind=='carry':
                    actual=next((w.get('id') for w in p.get('weapons',{}).values() if w.get('type')==p['weapon']),None)
                    if actual is not None and actual!=task.target['id']:
                        self.memory.outcome('carry_'+str(actual),'verified',s['t'],'The held entity ID confirms this actual mission item was collected.',3)
                        self._finish(s,'interrupted','Use collected a different nearby mission item; the requested item remains unconfirmed.',3);return
                self._finish(s,'verified','Inventory, ammunition or held mission object changed.');return
        if kind=='heal' and p['health']>old['health']+10 and 'slot3' not in p['inventory']:
            self._finish(s,'verified','Our medkit was consumed and our health increased.');return
        if kind=='boost' and 'slot4' not in p['inventory'] and p.get('temp_health',0)>old.get('temp_health',0)+5:
            self._finish(s,'verified','Boost consumed and temporary health increased.');return
        if kind=='rescue' and current and not (current['incap'] or current.get('ledge')):
            self._finish(s,'verified','Teammate is no longer down or hanging.');return
        if kind=='deliver' and old['weapon'] in CARRY_ITEMS and p['weapon'] not in CARRY_ITEMS and s['t']-self.started>1:
            poured=s.get('metrics',{}).get('pours',0)>self.baseline.get('metrics',{}).get('pours',0)
            cola_done=s['map']=='c1m2_streets' and mission(s)['carry_complete']
            if poured:
                consumed={w['id'] for w in old.get('weapons',{}).values() if w.get('type')=='weapon_gascan' and w.get('id') is not None}
                self.memory.consumed_fuel=getattr(self.memory,'consumed_fuel',set())|consumed
            self._finish(s,'verified' if poured or cola_done else 'needs_confirmation','Engine confirmed fuel pour.' if poured else ('The map emitted the cola-delivery completion event.' if cola_done else 'Carried object gone; mission completion still needs world evidence.'),10);return
        if kind=='use':
            if (task.target.get('type')=='trigger_finale' and mission(s)['finale_started']
                and not mission(self.baseline)['finale_started']):
                self._finish(s,'input_accepted','Engine confirmed the finale started; escape completion is separately verified.',6);return
            previous=task.target
            if self.last_use>=self.started and consumed_control(s,previous):
                self._finish(s,'input_accepted','Known single-use control disappeared after Use; chapter progress remains separately verified.',6);return
            if current and current.get('door_state')!=previous.get('door_state'):
                self._finish(s,'verified','Door state changed.');return
            used=s.get('metrics',{}).get('last_use')
            if used and used['target']==previous['id'] and used['t']>=self.started:
                self._finish(s,'input_accepted','Engine observed Use on the intended entity; chapter progress is separately verified.',6);return
        if s['t']-self.started>task.timeout:
            self._finish(s,'failed','Skill deadline reached without its required outcome.',20)
    def safe_escape(self,s,avoid=()):
        self.nav.update(s)
        p=s['player'];candidates=[];nearby=[]
        clear={a['id'] for a in s.get('nearby_nav',[]) if not a['damaging'] and not a['blocked']}
        eye_height=p['eye'][2]-p['p'][2]
        def separation(point,e):return distance([point[0],point[1],point[2]+eye_height],e['p'])
        def away(point,lateral=False):
            return all(separation(point,e)>separation(p['p'],e)+(40 if lateral else 120)
                and (lateral or sum((point[i]-p['p'][i])*(p['p'][i]-e['p'][i]) for i in (0,1))>0) for e in avoid)
        # During an actual evacuation, use the boarding route as our retreat
        # when it also clears the Tank. A generic retreat previously led the
        # team past the helicopter into a dead end. Keep the same observed
        # floor, clearance, height and bounded-distance requirements.
        rescue_goal=self.nav.goal
        evacuating=(mission(s)['phase']=='escape' and (self.memory.navigation_goal or {}).get('rescue')
            and rescue_goal in self.nav.areas)
        rescue_destination=self.nav.areas[rescue_goal]['p'] if evacuating else None
        if avoid and evacuating and rescue_goal in clear:
            destination=self.nav.areas[rescue_goal]['p']
            boarding=flat_distance(p['p'],destination)<70 and abs(p['p'][2]-destination[2])<30
            if boarding or away(destination,True):
                try:rescue_path=self.nav.route(p['area'],rescue_goal,escaping=True)
                except RuntimeError:rescue_path=[]
                length=sum(distance(self.nav.areas[u]['p'],self.nav.areas[v]['p']) for u,v in zip(rescue_path,rescue_path[1:]))
                if (rescue_path and length<=1000 and all(i in clear for i in rescue_path[1:])
                    and all(separation(self.nav.areas[i]['p'],e)>=160 for i in rescue_path[1:] for e in avoid)):
                    self.escape_goal=rescue_goal
                    return self.nav.waypoint(s,rescue_goal,escaping=True)
        if (self.escape_goal in clear and self.escape_goal!=p['area']
            and (not avoid or away(self.nav.areas[self.escape_goal]['p'],True))
            and (not evacuating or distance(self.nav.areas[self.escape_goal]['p'],rescue_destination)
                <=distance(p['p'],rescue_destination)+20)):
            try:return self.nav.waypoint(s,self.escape_goal,escaping=True)
            except RuntimeError:self.escape_goal=None
        for item in s.get('nearby_nav',[]):
            a=self.nav.areas.get(item['id'])
            if not a or item['damaging'] or item['blocked']:continue
            if abs(a['p'][2]-p['p'][2])>500 or not 20<flat_distance(a['p'],p['p'])<900:continue
            if avoid and not away(a['p'],True):continue
            nearby.append((distance(a['p'],p['p']),a))
        # Bound physical travel instead of counting tiny nav polygons or
        # rejecting safe stair flights solely for changing elevation.
        nearby=sorted(nearby,key=lambda pair:pair[0])[:96]
        routes=self.nav.routes_to(p['area'],[a['id'] for _,a in nearby],escaping=True)
        for _,a in nearby:
            route=routes.get(a['id'])
            if route is None:continue
            length=sum(distance(self.nav.areas[u]['p'],self.nav.areas[v]['p']) for u,v in zip(route,route[1:]))
            # Stairs can first pass underneath a higher-floor Tank. Compare
            # actual body-height separation, allowing a short approach around
            # the landing while still rejecting a route through its body.
            if avoid and any(separation(self.nav.areas[i]['p'],e)<min(160,max(30,separation(p['p'],e)-60)) for i in route[1:] for e in avoid):continue
            # A wall can rule out straight retreat. Allow a walkable lateral
            # turn that still increases separation; keep straight retreat
            # preferred and never route significantly closer to the Tank.
            if length<=1000:
                score=length+(200 if avoid and not away(a['p']) else 0)
                # A short retreat along a railing is slow under the motor's
                # required precision walking, and left us beside a Tank.
                # Score the whole path, not just destination separation.
                score+=2*sum(distance(self.nav.areas[u]['p'],self.nav.areas[v]['p'])
                    for u,v in zip(route,route[1:])
                    if (self.nav.areas[u].get('attr',0)|self.nav.areas[v].get('attr',0))&(4|64|32768))
                if avoid:
                    # The nearest clear landing can be a dead end only a few
                    # steps farther from the Tank. Prefer a sustained retreat
                    # over repeatedly ending the route on those tiny polygons.
                    # Keep short/lateral escapes available when nothing better
                    # is reachable; the same body-clearance checks still apply.
                    gain=min(separation(a['p'],e)-separation(p['p'],e) for e in avoid)
                    score+=3*max(0,250-gain)
                if evacuating:
                    # Safe local alternatives can lead in opposite directions.
                    # Prefer the one that approaches the observed rescue rather
                    # than extending a Tank kite into the waterfront dead end.
                    # This ranks only routes that passed the existing guards.
                    score+=2*distance(a['p'],rescue_destination)
                candidates.append((score,a['id']))
        if not candidates:raise NoEscapeRoute('No validated nearby escape route; visual planner needed')
        self.escape_goal=min(candidates)[1]
        return self.nav.waypoint(s,self.escape_goal,escaping=True)
    def recover_escape(self,s):
        """Try a different destination when the only path to this one stalls."""
        previous_goal=self.escape_goal
        if previous_goal is None:return None
        try:self.nav.waypoint(s,previous_goal,escaping=True)
        except NoRouteError:return None
        if len(self.nav.path)<2:return None
        edge=tuple(self.nav.path[:2])
        already_avoided=edge in self.nav.avoided
        self.nav.avoided.add(edge);self.nav.cached_start=None;self.escape_goal=None
        tanks=retreat_tanks(s,already_retreating=bool(self.task and self.task.kind=='evade'))
        if not tanks and s['t']<self.tank_retreat_until:tanks=self.remembered_tanks
        try:self.safe_escape(s,tanks)
        except NoEscapeRoute:
            if not already_avoided:self.nav.avoided.discard(edge)
            self.nav.cached_start=None;self.escape_goal=previous_goal
            return None
        return {'edge':list(edge),'alternate_goal':self.escape_goal,'alternate_length':len(self.nav.path)}
    def tick(self,s,decision):
        p=s['player'];t=s['t'];self._verify(s)
        proposed=decision['task'] if decision else Task('hold','hold','Await a fresh tactical decision.')
        if proposed.kind=='carry' and p['weapon'] in CARRY_ITEMS:
            proposed=Task('route','route','A mission object is already held; travel to its delivery point.')
        if proposed.kind=='carry' and proposed.target['id'] in getattr(self.memory,'consumed_fuel',()):
            proposed=Task('route','route','That fuel was already delivered; continue to the current mission goal.')
        if proposed.kind=='deliver' and p['weapon'] not in CARRY_ITEMS:
            proposed=Task('hold','hold','The mission object is no longer held; defend and reacquire it.')
        if proposed.kind=='stage' and p['weapon']!='weapon_gascan':
            proposed=Task('hold','hold','No fuel is held; do not apply the throw button to a firearm.')
        if not self.memory.allowed(proposed.key,t):proposed=Task('hold','hold','Reevaluate the previous outcome.')
        forced_hazard=p['on_fire'] or p['area_damaging']
        tanks=retreat_tanks(s,already_retreating=bool(self.task and self.task.kind=='evade'))
        if tanks:
            self.remembered_tanks=[dict(e,p=list(e['p'])) for e in tanks]
            self.tank_retreat_until=t+3
        elif any(e['type']==8 for e in s['enemies']):
            # A currently visible distant Tank is fresh evidence that the
            # retreat threshold cleared. Brief occlusion is not that evidence.
            self.tank_retreat_until=t
        inactive={e['id'] for e in s.get('inactive_enemies',[]) if e['type']==8}
        self.remembered_tanks=[e for e in self.remembered_tanks if e['id'] not in inactive]
        escape_tanks=tanks
        if not tanks and self.task and self.task.kind=='evade' and t<self.tank_retreat_until:
            escape_tanks=self.remembered_tanks
        forced_tank=bool(escape_tanks) and not forced_hazard
        if not forced_hazard and not forced_tank:self.escape_goal=None
        if forced_hazard:proposed=Task('escape','escape','Escape damaging ground.')
        elif forced_tank:proposed=Task('evade','evade','Retreat from the nearby Tank while fighting.')
        elif proposed.kind in ('escape','evade'):
            # A response can still be within its TTL after the Tank becomes
            # inactive or the hazard clears. Do not keep seeking arbitrary
            # retreat points without the threat that justified that response.
            proposed=Task('hold','hold','The immediate retreat trigger cleared; await a current tactical decision.')
        if (self.task and self.task.kind in ('use','loot','carry','deliver')
            and proposed.kind=='use' and proposed.target.get('door_state')==0
            and self.task.key!=proposed.key and distance(p['eye'],proposed.target['p'])<160):
            self._finish(s,'interrupted','Open the observed access door before continuing the farther task.',1)
        if not self.task or self.task.kind in ('route','hold','escape','evade') or forced_hazard or forced_tank:
            if not self.task or self.task.key!=proposed.key:self._start(proposed,s)
        task=self.task or proposed;kind=task.kind;keys=[];buttons=[];point=None;waypoint=None;target_fresh=True;raw_point=None
        interaction_range=75 if kind=='deliver' else 108
        if kind=='stage' and p['weapon']!='weapon_gascan':
            return {'keys':[],'buttons':[],'dx':0,'dy':0,'task':kind,'reason':'Release fire after the can leaves the hand.'}
        if kind=='deliver' and p['weapon'] not in CARRY_ITEMS:
            return {'keys':[],'buttons':[],'dx':0,'dy':0,'task':kind,'reason':'Carried object gone; release fire while checking the actual pour outcome.'}
        self.memory.current_task={'key':task.key,'kind':kind,'elapsed_seconds':round(t-self.started,1)}
        # Long holds are interruptible when a threat reaches striking range.
        close_attackers=[e for e in s['enemies'] if e['type']!=7 and distance(p['eye'],e['p'])<160]
        if kind=='carry' and close_attackers and (self.memory.recent_damage>0 or p['health']+p.get('temp_health',0)<40):
            self._finish(s,'interrupted','Clear the immediate attackers before picking up fuel again.',5)
            self._start(Task('hold','hold','Defend before resuming the pickup.'),s)
            task=self.task;kind='hold'
        urgent_delivery_threat=self.memory.recent_damage>=8 or any(e['type'] in (1,3,5,6,8) for e in close_attackers)
        if kind in ('heal','rescue','deliver','stage') and close_attackers and (kind not in ('deliver','stage') or urgent_delivery_threat):
            self._finish(s,'interrupted','Immediate attacker interrupted the support action.',2)
            self._start(Task('hold','hold','Defend before resuming the support action.'),s)
            task=self.task;kind='hold'
        pitch=5;aim=None
        target,combat=self.combat_focus.select(s,decision)
        freeing_teammate=bool(target and target['type'] in (1,3,5,6)
            and any(b.get('pinned') and flat_distance(b['p'],target['p'])<170 for b in s['teammates']))
        if tanks and not freeing_teammate:
            # Retreat and fire must refer to the same urgent threat. A common
            # infected can still trigger the independent close-range shove.
            target=min(tanks,key=lambda e:distance(p['eye'],e['p']));combat='fire'
        # Healing, reviving and pouring can lock movement themselves. Releasing
        # their held input when that happens cancels the very action we began.
        support_lock=(kind in ('heal','boost') or (kind in ('deliver','rescue')
            and task.target and distance(p['eye'],task.target['p'])<110))
        if p['dead'] or p['pinned'] or p['ledge'] or (p.get('immobilized') and not p['incap'] and not support_lock):
            return {'keys':[],'buttons':[],'dx':0,'dy':0,'task':kind,'reason':'character cannot act normally'}
        if p.get('immobilized') and support_lock:
            return {'keys':['e'] if kind=='rescue' else [],'buttons':[] if kind=='rescue' else ['fire'],
                'dx':0,'dy':0,'task':kind,'reason':'Continue the already-started support hold without fighting the interaction camera.'}
        if kind in ('route','escape','evade'):
            try:
                # Last-seen threats sustain a short retreat around cover;
                # only the fresh `tanks` list can select a shooting target.
                waypoint=self.safe_escape(s,escape_tanks) if kind=='evade' else self.safe_escape(s) if kind=='escape' else self.nav.waypoint(s)
            except NoEscapeRoute:
                if kind!='evade' or p['on_fire'] or p['area_damaging']:raise
                # No invented escape through a wall or drop. Defend from the
                # current floor while seeking a new route on every frame. The
                # existing three-second blocked-retreat check bounds this hold.
                self.escape_goal=None
                waypoint={'p':list(p['p']),'area':p['area'],'attr':0,
                    'link_type':'walk','escape_unavailable':True}
            point=waypoint['p'];pitch=waypoint.get('pitch',5)
        elif kind=='stage':
            standing=task.target['p']
            near=flat_distance(p['p'],standing)
            # A narrow reviewed opening does not tolerate a broad arrival
            # radius. Reposition after pre-throw drift instead of retaining
            # arrival while the player has moved behind an adjacent wall.
            arrived=near<12 and abs(p['p'][2]-standing[2])<15
            # Throw animation finishes after the click. Preserve its aim and
            # standing position until the can is actually released, including
            # ordinary deceleration or a small shove beyond the arrival radius.
            arrived=arrived or self.stage_fired_t is not None
            self.stage_arrived=arrived
            if arrived:
                point=standing;aim=task.target['aim'];pitch=look(p['eye'],aim)[0]
            else:
                area=self.nav.closest_area(standing)
                waypoint=self.nav.waypoint(s,area) if area!=p['area'] else None
                point=waypoint['p'] if waypoint else standing
        elif kind in ('loot','carry','use','deliver','rescue'):
            current=self._entities(s).get(task.target['id'])
            if current is None:
                target_fresh=False
                # Brief occlusion while walking around a corner should not
                # erase an observed supply. Travel to its last position, but
                # require fresh observation again before pressing Use/fire.
                if kind in ('loot','carry','use','deliver') and t-self.started<12:
                    current=task.target
                else:
                    if t-self.started>2:
                        if kind=='carry':
                            self._finish(s,'interrupted','Fuel is occluded; return to the remembered route and reacquire before attempting pickup.',2)
                        else:
                            self._finish(s,'failed','Target is no longer observed; reacquire it before acting.',4)
                    return {'keys':[],'buttons':[],'dx':0,'dy':0,'task':kind,'reason':'target absent'}
            raw_point=list(current['p'])
            if (kind=='use' and current.get('name')=='button_inelevator'
                and mission(s).get('transport')=='lift_operate'
                and not current.get('disabled') and distance(p['eye'],raw_point)<80):
                # The decorative button model obscures its invisible brush
                # center. Astra visually checked it; ordinary Use411 worked
                # from this same deck. Require the actual stopped platform,
                # whole-team unlock and close range before this narrow probe.
                current=dict(current,visible=True)
            if current.get('visible') is False:target_fresh=False
            if kind=='rescue':raw_point[2]+=30
            point=raw_point
            standing_point=list(current['p'])
            if kind=='deliver':standing_point[2]-=max(40,min(64,p['eye'][2]-p['p'][2]))
            approach_range=interaction_range if kind in ('loot','carry') else 220
            needs_route=(distance(p['eye'],point)>approach_range or abs(point[2]-p['eye'][2])>100
                or not target_fresh or current.get('visible') is False)
            vertical_delivery=kind=='deliver' and abs(point[2]-p['eye'][2])>interaction_range-5
            # A thin wall can hide an item inside interaction distance. Its
            # reachable floor still requires the doorway route before Use.
            if needs_route or vertical_delivery:
                try:
                    area=self.nav.closest_area(standing_point,below=kind in ('loot','carry'),
                        reachable_from=p['area'] if kind in ('loot','carry') else None)
                except NoRouteError:
                    if kind not in ('loot','carry'):raise
                    self._finish(s,'interrupted','The observed pickup has no currently reachable floor; continue the accessible mission route.',8)
                    return {'keys':[],'buttons':[],'dx':0,'dy':0,'task':'hold','reason':'pickup route unavailable'}
                if area!=p['area']:
                    waypoint=self.nav.waypoint(s,area);point=waypoint['p']
            aim=raw_point
        if point is not None and aim is None:
            # A lateral alignment point can be only a few units away. Looking
            # at it makes every small overshoot demand a180degree camera turn
            # while the following tick wants to face down the corridor again.
            # Keep looking into the destination floor; steer the small lateral
            # correction independently. Fresh combat targets still override.
            facing=(self.nav.areas.get(waypoint.get('area'),{}).get('p',point)
                    if waypoint and waypoint.get('align_portal') else point)
            aim=[facing[0],facing[1],p['eye'][2]]
        # Being close through the car body or a wall is not arrival. Keep
        # following the approach until the actual interaction is visible;
        # otherwise a pour can freeze movement without ever pressing fire.
        committed=(kind in ('heal','boost') or (kind in ('deliver','rescue','use')
            and target_fresh and waypoint is None and raw_point is not None
            and distance(p['eye'],raw_point)<(interaction_range if kind=='deliver' else 110)))
        if kind=='stage' and arrived:committed=True
        ladder=waypoint and waypoint.get('link_type')=='ladder'
        carrying=p['weapon'] in CARRY_ITEMS
        carry_emergency=bool(target and ((self.memory.recent_damage>=10 and distance(p['eye'],target['p'])<300)
            or (p['health']+p.get('temp_health',0)<40 and distance(p['eye'],target['p'])<200)
            or (target['type'] in (6,8) and distance(p['eye'],target['p'])<(800 if target['type']==6 else 550))
            or any(b.get('pinned') and flat_distance(b['p'],target['p'])<170 for b in s['teammates'])))
        carry_shove=bool(target and target['type'] not in (6,7,8) and distance(p['eye'],target['p'])<155)
        # Recheck the selected target every feedback tick. Never attack a newly
        # calm Witch, disappeared enemy, or a teammate who crossed the ray.
        # Carrying fuel past distant infected requires no aiming at them.
        # Keep the route in view until an actual shove or weapon switch is due.
        if target and (not carrying or carry_emergency or carry_shove) and not committed and not ladder and not (target['type']==7 and target.get('rage',0)<.8):
            aim=[target['p'][i]+target.get('velocity',[0,0,0])[i]*.08 for i in range(3)]
            if riot_front(s,target) and distance(p['eye'],target['p'])<155:
                # Aim an ordinary close shove off-center; reobserve its facing
                # before permitting shots at the exposed rear.
                forward=target.get('forward',[1,0,0])
                aim[0]-=forward[1]*14;aim[1]+=forward[0]*14
            pitch=look(p['eye'],aim)[0]
        elif aim is not None and kind in ('loot','carry','use','deliver','rescue') and not ladder:
            pitch=look(p['eye'],aim)[0]
        if ladder:
            # An interaction on another floor still requires ordinary ladder
            # travel. Its distant target must not steer the climb camera.
            aim=[point[0],point[1],p['eye'][2]]
            pitch=waypoint.get('pitch',0)
        desired=look(p['eye'],aim)[1] if aim is not None else p['angles'][1]
        if ladder and waypoint.get('phase')=='climb' and p.get('on_ladder'):
            desired=waypoint.get('face_yaw',desired)
        yaw_error=wrap(desired-p['angles'][1]);pitch_error=max(-85,min(85,pitch))-p['angles'][0]
        turn_limit=60 if target and not committed and not ladder else 24
        yaw_step=max(-turn_limit,min(turn_limit,yaw_error));pitch_step=max(-turn_limit,min(turn_limit,pitch_error))
        predicted_yaw=p['angles'][1]+yaw_step
        if kind=='stage' and arrived and abs(yaw_error)<3 and abs(pitch_error)<3:
            settled=math.hypot(*p['velocity'][:2])<15
            if settled:
                if self.stage_ready_since is None:self.stage_ready_since=t
                if t-self.stage_ready_since>=.2:
                    buttons=['fire']
                    if self.stage_fired_t is None:self.stage_fired_t=t
            else:self.stage_ready_since=None
        elif kind=='stage':self.stage_ready_since=None
        if point is not None and not committed and not p['incap']:
            stopping=3 if waypoint and waypoint.get('align_portal') else (12 if kind in ('route','escape','evade') or waypoint else 45 if kind=='deliver' else 65)
            if ladder and waypoint.get('phase')=='approach':stopping=3
            if ladder and waypoint.get('phase')=='dismount':stopping=2
            if kind=='stage' and not waypoint:stopping=8
            keys+=movement_keys(p['p'],point,predicted_yaw,stopping)
            # Preserve running on open retreats, but resolve a tiny portal
            # correction with bounded walking even when a Tank is pursuing.
            # Holding lateral movement across that corner can leave the floor.
            if (keys and waypoint and waypoint.get('align_portal')
                and (flat_distance(p['p'],point)<70
                    or (kind not in ('escape','evade') and mission(s)['phase']!='escape'
                        and not mission(s)['gauntlet_active']))):keys.append('shift')
            floor_flags=self.nav.areas.get(p['area'],{}).get('attr',0)|(waypoint.get('attr',0) if waypoint else 0)
            # Valve PRECISE, WALK and CLIFF flags identify navigation where
            # full-speed lateral corrections can overshoot the usable floor.
            # Keep this local precision even during a running finale.
            if keys and waypoint and not ladder and floor_flags&(4|64|32768) and 'shift' not in keys:
                keys.append('shift')
            if keys and ladder and waypoint.get('phase')=='approach' and flat_distance(p['p'],point)<90 and 'shift' not in keys:keys.append('shift')
            if (ladder and waypoint.get('phase')=='dismount' and p.get('on_ladder')
                and flat_distance(p['p'],point)<20 and abs(p['p'][2]-point[2])<35
                and t-self.last_jump>.65):
                # A small landing can leave the player attached within the
                # usual stopping radius. Hop off only at this observed floor.
                keys.append('space');self.last_jump=t
            if ladder and waypoint.get('phase')=='climb':
                keys=['w'] if abs(yaw_error)<40 else []
                # Looking diagonally at a point behind a narrow ladder can
                # pin the survivor on its side rail. Face its actual normal
                # and correct lateral alignment independently while climbing.
                if p.get('on_ladder') and abs(yaw_error)<10 and waypoint.get('ladder_center'):
                    center=waypoint['ladder_center'];angle=math.radians(predicted_yaw)
                    lateral=-(center[0]-p['p'][0])*math.sin(angle)+(center[1]-p['p'][1])*math.cos(angle)
                    if abs(lateral)>4:keys.append('a' if lateral>0 else 'd')
            elif waypoint:
                if waypoint.get('attr',0)&1:keys.append('ctrl')
                current_floor=self.nav.areas.get(p['area'],{})
                next_floor=self.nav.areas.get(waypoint.get('area'),{})
                continuous_slope=False
                if all('nw' in a and 'se' in a for a in (current_floor,next_floor)):
                    continuous_slope=(any(abs(a['nw'][2]-a['se'][2])>18 for a in (current_floor,next_floor))
                        and min(next_floor['nw'][2],next_floor['se'][2])-max(current_floor['nw'][2],current_floor['se'][2])<=18
                        and all(max(current_floor['nw'][i],next_floor['nw'][i])<=min(current_floor['se'][i],next_floor['se'][i]) for i in (0,1)))
                # A higher waypoint along continuous stairs is reached by
                # walking. Repeated jumps here landed on the center railing
                # and made the nearest-area lookup switch to the other flight.
                # The next nav center can sit behind the lip of a short stair
                # flight. Waiting until90units reached can leave the hull stuck
                #115units from its landing, as observed in the Streets stairwell.
                if not floor_flags&8 and not continuous_slope and 18<point[2]-p['p'][2]<65 and flat_distance(point,p['p'])<140 and abs(p['velocity'][2])<12 and t-self.last_jump>.65:
                    keys.append('space');self.last_jump=t
        # Combat may turn aim away from a distant interaction. Never interpret
        # a nearby enemy as the delivery target and throw the carried can.
        aim_tolerance=2 if kind in ('deliver','loot','carry') else 12
        pitch_tolerance=2 if kind=='deliver' else 3 if kind in ('loot','carry') else 14
        if target_fresh and raw_point is not None and aim==raw_point and distance(p['eye'],raw_point)<interaction_range and abs(yaw_error)<aim_tolerance and abs(pitch_error)<pitch_tolerance:
            if kind=='deliver':buttons=['fire'];keys=[]
            elif kind=='rescue':keys=['e']
            elif t-self.last_use>.3:keys.append('e');self.last_use=t
        if kind in ('heal','boost'):
            required='weapon_first_aid_kit' if kind=='heal' else p['inventory'].get('slot4')
            if p['weapon']!=required:keys=['4' if kind=='heal' else '5'];buttons=[]
            elif not p['incap']:keys=[];buttons=['fire']
        if not committed and not ladder:
            ammo=active_ammo(s)
            should_fight=target is not None and combat in ('fire','shove')
            primary=p.get('weapons',{}).get('slot0',{})
            primary_usable=(firearm(primary.get('type','')) and
                (primary.get('clip',0)>0 or primary.get('reserve',0)>0))
            secondary=p.get('weapons',{}).get('slot1',{})
            urgent_sidearm=(should_fight and target['type'] in (1,3,5,6,8)
                and distance(p['eye'],target['p'])<300 and primary.get('clip',0)==0
                and secondary.get('type') in ('weapon_pistol','weapon_pistol_magnum')
                and secondary.get('clip',0)>0 and not secondary.get('reloading'))
            if should_fight and carrying and not carry_emergency:
                # A firearm switch drops the mission object. Carry it past
                # ordinary distant infected, shoving close blockers normally.
                if target['type'] not in (6,7,8) and distance(p['eye'],target['p'])<155 and abs(yaw_error)<55 and t-self.last_shove>.65:
                    buttons=['shove'];self.last_shove=t
            elif urgent_sidearm and not p['incap'] and p['weapon']==primary.get('type'):
                # An empty rifle's reload can outlast a nearby special's next
                # attack. Use the loaded sidearm now, and retain it while that
                # immediate threat persists instead of switching straight back.
                keys.append('2');buttons=[]
            elif (should_fight and not p['incap'] and primary_usable and not urgent_sidearm
                and p['weapon'] in ('weapon_pistol','weapon_pistol_magnum')):
                # Empty-primary fallback must end after normal resupply.
                # The pistol has infinite reserves, so its own empty-ammo
                # branch will never switch back. Select the primary now;
                # fresh equipped-weapon feedback then drives its reload/fire.
                keys.append('1');buttons=[]
            elif should_fight and not firearm(p['weapon']):
                usable_primary=('slot0' in p['inventory'] and
                    (primary.get('clip',1)>0 or primary.get('reserve',-1)!=0))
                keys.append('1' if usable_primary else '2');buttons=[]
            elif should_fight and ammo['clip']==0 and ammo['reserve']==0:
                keys.append('2' if p['weapon']==p['inventory'].get('slot0') else '1')
            elif should_fight:
                d=distance(p['eye'],target['p'])
                tolerance=min(5,max(.4,math.degrees(math.atan2(24 if target['type']==8 else 10,d))))
                can_shove=not p['incap'] and target['type'] not in (6,7,8) and d<155
                if riot_front(s,target):
                    if can_shove and abs(yaw_error)<55 and t-self.last_shove>.65:buttons=['shove'];self.last_shove=t
                    elif ammo['clip']==0 and ammo['reserve']!=0:keys.append('r')
                elif (combat=='shove' or (target['type'] in (1,3,5) and d<140)) and can_shove:
                    if abs(yaw_error)<55 and t-self.last_shove>.65:buttons=['shove'];self.last_shove=t
                elif ammo['clip']==0:
                    if not ammo['reloading']:keys.append('r')
                elif abs(yaw_error-yaw_step)<tolerance and abs(pitch_error-pitch_step)<tolerance and not friendly_blocked(s,aim):
                    # Keep an automatic weapon's trigger held across its own
                    # cooldown. Engine firing cadence must not be gated by
                    # sparse readiness samples from the observation stream.
                    # The input layer applies the bounded aim correction
                    # before pressing fire. Test its remaining error, not the
                    # pre-correction angle that a moving target already left.
                    if p['weapon'] in ('weapon_pistol','weapon_pistol_magnum'):
                        if ammo['ready'] and t-self.last_semi_press>=.12:
                            buttons=['fire'];self.last_semi_press=t
                    else:buttons=['fire']
            elif combat=='reload' and firearm(p['weapon']) and not ammo['reloading'] and ammo['reserve']!=0 and (ammo['max_clip']<0 or ammo['clip']<ammo['max_clip']):
                keys.append('r')
        # Immediate close-range defense continues between network decisions.
        if not committed and not ladder and not p['incap']:
            close=next((e for e in s['enemies'] if e['type'] in (0,1,2,3,4,5)
                and not (is_riot(e) and not riot_front(s,e))
                and distance(p['eye'],e['p'])<(140 if e['type'] in (1,3,5) else 120)
                and abs(wrap(look(p['eye'],e['p'])[1]-p['angles'][1]))<50),None)
            if close and t-self.last_shove>.65:buttons=['shove'];self.last_shove=t
        return {'keys':list(dict.fromkeys(keys))[:4],'buttons':buttons,'dx':round(-yaw_step/.066),'dy':round(pitch_step/.066),
                'task':kind,'target_id':target['id'] if target else None,'weapon':p['weapon'],'waypoint':waypoint}
