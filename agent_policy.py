"""Focused Jev questions plus deterministic legality and arithmetic."""
from dataclasses import dataclass
from game_knowledge import CARRY_ITEMS,TIER2,firearm,role_for,mission
from navigation import distance,flat_distance,look,wrap,NoRouteError
from agent_state import active_ammo,perceive,needs_ammo_resupply,critical_ammo_resupply,engageable

@dataclass
class Task:
    key:str
    kind:str
    description:str
    target:dict|None=None
    timeout:float=18
    critical:bool=False

def tasks_for(state,nav,memory):
    p=state['player'];t=state['t'];inv=p['inventory'];ammo=active_ammo(state)
    urgent_travel=mission(state)['phase']=='escape' or mission(state)['gauntlet_active']
    supply_ammo=p.get('weapons',{}).get('slot0') or ammo
    resupply_route=bool((memory.navigation_goal or {}).get('critical_supply'))
    try:
        if mission(state)['phase']=='defend' and not resupply_route:
            waypoint={'p':list(p['p']),'area':p['area'],'attr':0,'link_type':'defend','remaining_areas':None}
        else:waypoint=nav.waypoint(state)
    except NoRouteError:
        urgent=(p['dead'] or p['pinned'] or p['incap'] or p['ledge'] or p['on_fire'] or p['area_damaging']
            or p.get('immobilized') or abs(p.get('velocity',[0,0,0])[2])>25
            or any(e['type']==8 and distance(p['eye'],e['p'])<550 for e in state['enemies']))
        if not urgent:raise
        # Immediate defense has its own observed local route. A temporarily
        # unavailable mission destination must not prevent that decision.
        waypoint={'p':list(p['p']),'area':p['area'],'attr':0,'link_type':'unavailable','remaining_areas':None}
    tasks={}
    inside_exit=bool(mission(state)['phase']=='safe_room' and nav.areas.get(p['area'],{}).get('spawn',0)&2048)
    checkpoints=[a for a in nav.areas.values() if a.get('spawn',0)&2048 and 'nw' in a]
    def teammate_inside(b):
        if b['dead']:return True
        pos=b['p']
        # Bots may stand against the room wall just beyond the nav rectangle.
        return any(a['nw'][0]-32<=pos[0]<=a['se'][0]+32 and a['nw'][1]-32<=pos[1]<=a['se'][1]+32
            and min(a['nw'][2],a['se'][2])-20<=pos[2]<=max(a['nw'][2],a['se'][2])+20
            and distance(pos,nav.areas[nav.goal]['p'])<600 for a in checkpoints)
    team_inside=inside_exit and all(teammate_inside(b) for b in state['teammates'])
    def add(task):
        if not memory.allowed(task.key,t):return
        if urgent_travel and task.kind=='loot' and task.target:
            # During the running finale, distant supplies repeatedly diverted
            # the team into a horde. Keep pickups on the immediate approach;
            # the mission route can bring farther supplies into range later.
            point=task.target['p']
            essential_ammo=task.target.get('type')=='weapon_ammo_spawn' and critical_ammo_resupply(p)
            essential_health=(p['health']+p.get('temp_health',0)<40
                and any(kind in task.target.get('type','') for kind in ('first_aid_kit','pain_pills','adrenaline')))
            if essential_ammo and resupply_route and distance(p['p'],point)>220:
                # Reach the remembered supply using its critical route first.
                # Starting the short Use skill686units away spent its whole
                # deadline approaching, then cooled down the needed refill.
                return
            if (distance(p['p'],point)>(800 if essential_ammo else 220) or abs(point[2]-p['p'][2])>100
                or task.target.get('visible') is False):return
            # A nearby medkit can still require several safe portal turns.
            # Six seconds expired before reaching the table at critical HP.
            # Keep the same visible, close, same-floor gate and bounded skill.
            task.timeout=min(task.timeout,18 if essential_health else 12 if essential_ammo else 6)
            if essential_health:task.critical=True
        if task.kind in ('loot','carry') and task.target:
            point=task.target['p']
            if distance(p['eye'],point)>108 or abs(point[2]-p['eye'][2])>100:
                try:nav.closest_area(point,below=True,reachable_from=p['area'])
                except NoRouteError:return
        tasks[task.key]=task
    goal=memory.navigation_goal or {}
    purpose=goal.get('purpose','Travel toward the current objective.')
    add(Task('route','route',purpose+' Continue fighting while moving; combat alone does not require standing still.',critical=bool(goal.get('critical_supply'))))
    add(Task('hold','hold','Stay with the team briefly when waiting, defending, or unable to act.'))
    if (p['dead'] or p['pinned'] or p['incap'] or p['ledge'] or p.get('immobilized')
        or (waypoint.get('link_type')=='unavailable' and abs(p.get('velocity',[0,0,0])[2])>25)):
        return {'hold':tasks['hold']},waypoint
    if mission(state).get('transport') in ('arriving','gathering','riding'):
        # Combat remains independent of this movement decision. Stay on the
        # dock/deck instead of detouring or walking into the water.
        return {'hold':tasks['hold']},waypoint
    if p.get('on_ladder') or waypoint.get('link_type')=='ladder':
        return {'route':tasks['route']},waypoint
    if p['on_fire'] or p['area_damaging']:
        return {'escape':Task('escape','escape','Leave the damaging ground immediately using a nearby clear, safe surface.',critical=True)},waypoint
    if any(e['type']==8 and distance(p['eye'],e['p'])<550 for e in state['enemies']):
        return {'evade':Task('evade','evade','Keep moving away from the nearby Tank on walkable ground while shooting it. Do not stand still to revive, loot or pour until clear.',critical=True)},waypoint
    if p['health']+p.get('temp_health',0)<50 and inv.get('slot4') in ('weapon_pain_pills','weapon_adrenaline'):
        return {'boost':Task('boost','boost','Use the carried health boost immediately to restore survival margin and movement speed.',timeout=5,critical=True)},waypoint
    threats=[e for e in state['enemies'] if e['type']!=7 and distance(p['eye'],e['p'])<200]
    if p['health']+p.get('temp_health',0)<60 and inv.get('slot3')=='weapon_first_aid_kit' and not threats:
        add(Task('heal','heal','Health is low and there is no immediate melee attacker. Use this clear interval to heal before continuing travel or collecting more fuel. Hold primary fire on the medkit until healing completes.',timeout=10,critical=True))
    if p['health']+p.get('temp_health',0)<60 and inv.get('slot4') in ('weapon_pain_pills','weapon_adrenaline'):
        add(Task('boost','boost','Use carried pills or adrenaline now for temporary health and mobility.',timeout=5))
    for b in state['teammates']:
        if not b['dead'] and (b['incap'] or b.get('ledge')) and not b['pinned']:
            key=f'rescue_{b["id"]}'
            if mission(state)['phase']=='escape':
                interrupted=sum(row['task']==key and row['status']=='interrupted'
                    and 0<=t-row['game_t']<30 for row in memory.outcomes)
                if (p['health']+p.get('temp_health',0)<40 or threats
                    or distance(p['p'],b['p'])>160 or interrupted>=2):continue
            add(Task(f'rescue_{b["id"]}','rescue','Reach the downed teammate and hold Use to revive; clear immediate attackers first.',b,timeout=25,critical=True))
    if mission(state).get('transport') in ('lift_arriving','lift_gathering','lift_riding'):
        # Retain medicine, nearby aid and emergency defense during transport;
        # no repeat call, distant pickup or walking onto an absent platform.
        return {k:v for k,v in tasks.items() if k!='route' and
            (v.kind!='rescue' or distance(v.target['p'],p['p'])<120)},waypoint
    items={i['id']:i for i in state.get('items',[])+state.get('interactables',[]) if not i.get('owned')}
    carry=p['weapon'] in CARRY_ITEMS
    near_occupied_exit=bool(state.get('exit_checkpoint_occupied') and
        (distance(p['p'],nav.areas[nav.goal]['p'])<600 or
         any(distance(p['p'],a['p'])<450 and distance(a['p'],nav.areas[nav.goal]['p'])<600
             for a in checkpoints)))
    if (not carry and not threats and mission(state)['phase']!='escape' and not near_occupied_exit
        and p['health']+p.get('temp_health',0)<30
        and inv.get('slot3')!='weapon_first_aid_kit'
        and any(b.get('bot') and b.get('has_medkit') and b['health']>=40
            and not (b['dead'] or b['incap'] or b['pinned'] or b.get('ledge'))
            and distance(p['p'],b['p'])<250 for b in state['teammates'])):
        add(Task('await_aid','hold','Critically wounded with no own medkit. A nearby standing bot has a medkit and adequate health. Stop briefly near the team so it can heal you; continue defending. Healing is not guaranteed and this wait is bounded.',timeout=12,critical=True))
    dangerous_pickup=any(e['type']==6 and distance(p['eye'],e['p'])<800 for e in state['enemies'])
    dangerous_pickup=dangerous_pickup or bool(threats and (memory.recent_damage>0 or p['health']+p.get('temp_health',0)<40))
    stage=getattr(memory,'staging_target',None) if p['weapon']=='weapon_gascan' else None
    if stage:
        add(Task('stage_'+str(stage['id']),'stage','Carry the gas can to the visually reviewed balcony point, stop moving, and throw toward the reviewed open floor. Release fire as soon as the can leaves the hand.',stage,35,True))
        tasks.pop('route',None)
    for item in sorted(items.values(),key=lambda i:distance(i['p'],p['p'])):
        kind=item['type'];role=role_for(state,item);d=distance(item['p'],p['p'])
        # Starting checkpoint doors can report locked while visibly offering
        # ordinary player Use. Do not mistake that flag for a missing action.
        if (item.get('locked') and kind!='prop_door_rotating_checkpoint') or item.get('disabled'):continue
        label=f'{kind} {round(d)} units away'
        if 'first_aid_kit' in kind and 'slot3' not in inv and not carry:
            add(Task(f'loot_{item["id"]}','loot','Collect a missing first aid kit: '+label,item))
        elif any(x in kind for x in ('pain_pills','adrenaline')) and 'slot4' not in inv and not carry:
            add(Task(f'loot_{item["id"]}','loot','Collect a missing health boost: '+label,item))
        elif kind=='weapon_ammo_spawn' and (needs_ammo_resupply(p) or item['id']==getattr(memory,'requested_ammo_refill',None)) and not carry:
            add(Task(f'loot_{item["id"]}','loot','Refill low firearm ammunition: '+label,item))
        elif (kind=='weapon_spawn' or kind.endswith('_spawn') or firearm(kind)) and not carry:
            gun=kind+' '+item.get('model','').lower()
            empty_primary=supply_ammo.get('clip',0)==0 and supply_ammo.get('reserve',-1)==0
            upgrade='slot0' not in inv or empty_primary or (not any(x in inv.get('slot0','') for x in TIER2) and any(x in gun for x in ('rifle','autoshot','spas')))
            automatic_rifle='rifle' in gun and not any(x in gun for x in ('hunting','sniper','scout','awp'))
            if automatic_rifle and any(x in inv.get('slot0','') for x in ('shotgun','spas','hunting','sniper','scout','awp')):upgrade=True
            if 'rifle_ak47' in gun and inv.get('slot0')=='weapon_rifle_desert':upgrade=True
            if upgrade and any(x in gun for x in ('rifle','smg','shotgun','autoshot')):
                add(Task(f'loot_{item["id"]}','loot','Equip a missing or stronger primary gun: '+label+'. Observed weapon model: '+item.get('model',kind),item))
        if role in ('collect cola','collect fuel') and not carry and not dangerous_pickup and not mission(state)['carry_complete']:
            if role=='collect fuel' and item['id'] in getattr(memory,'consumed_fuel',()):continue
            # The mission planner owns long trips to remembered fuel. Offering
            # a pickup through the atrium from another floor repeatedly made
            # Jev abandon that route and time out when the can became occluded.
            # Only turn travel into a pickup on the final, nearby approach.
            if role=='collect fuel' and (d>220 or abs(item['p'][2]-p['p'][2])>80):continue
            floors=getattr(memory,'fuel_stage_floors',None)
            if (role=='collect fuel' and getattr(memory,'fuel_stage_upper',False) and floors is not None
                and not any(abs(item['p'][2]-z)<80 for z in floors)):continue
            if not (role=='collect fuel' and getattr(memory,'fuel_stage_upper',False) and item['p'][2]<180):
                add(Task(f'carry_{item["id"]}','carry','Pick up the mission item; carry it to its delivery target. '+role+': '+label,item,25,True))
        elif role in ('deliver cola','pour fuel') and carry and not mission(state)['carry_complete']:
            # The planner already routes a carried object to this landmark.
            # Only offer the final interaction nearby; otherwise it competes
            # with identical travel and interrupts transport during combat.
            if not stage and distance(p['eye'],item['p'])<160 and item.get('visible') is not False:
                add(Task(f'deliver_{item["id"]}','deliver',f'The delivery target is {round(distance(p["eye"],item["p"]))} units away. Aim at it and hold primary fire until the game accepts the carried object. '+role,item,15,True))
        elif role=='door':
            # Ordinary interior doors now share the observed interaction list.
            # Only a checkpoint door can seal the safe room and end a chapter.
            closing=(kind=='prop_door_rotating_checkpoint' and item.get('door_state')==2 and team_inside
                and d<220 and abs(item['p'][2]-p['eye'][2])<110)
            if closing or (item.get('door_state')==0 and d<200):
                add(Task(f'use_{item["id"]}','use','All living teammates are now inside the exit checkpoint. Close its door to finish the chapter.' if closing else 'Open this nearby closed door before traveling onward or attempting controls behind it.',item,10,True))
        elif role and role not in ('collect cola','collect fuel','deliver cola','pour fuel'):
            if role=='start bridge crossing' and mission(state)['bridge_confirmation_pending']:continue
            if role=='call elevator' and mission(state).get('transport'):continue
            if mission(state)['finale_started'] and role in ('call rescue boat','confirm rescue finale',
                'start rescue finale','contact rescue','activate stage lights','answer bridge radio','start bridge crossing'):continue
            if kind=='trigger_finale' and mission(state)['finale_started']:continue
            if distance(p['eye'],item['p'])<200:
                add(Task(f'use_{item["id"]}','use','Required nearby mission control: '+role+'. Approach it to obtain a clear view, then operate it before continuing toward the chapter exit.',item,20,True))
    if inside_exit and distance(p['p'],nav.areas[nav.goal]['p'])<100:
        tasks.pop('route',None)
    if mission(state)['phase']=='defend' and not resupply_route:tasks.pop('route',None)
    if any(v.kind=='deliver' and distance(p['eye'],v.target['p'])<75 for v in tasks.values()):
        # Travel is already complete; retaining it hid the actual next step
        # behind the policy's preference for continuing unfinished movement.
        tasks.pop('route',None)
    if any(v.kind=='use' and v.target.get('visible') is True
        and distance(p['eye'],v.target['p'])<100
        and role_for(state,v.target) not in (None,'door') for v in tasks.values()):
        # The required control is now within normal Use reach. Continuing the
        # completed approach made Jev orbit the radio instead of contacting it.
        # Keep healing, supply and teammate choices; remove only idle travel.
        tasks.pop('route',None)
    closed_doors=[v for v in tasks.values() if v.kind=='use' and v.target.get('door_state')==0]
    if closed_doors:
        # A required button behind a closed entrance is not presently usable.
        # Let Jev open the observed access door before selecting that control.
        tasks={k:v for k,v in tasks.items() if not (v.kind=='use' and v.target.get('visible') is False and v not in closed_doors)}
        heading=look(p['p'],waypoint['p'])[1]
        ray=min(state.get('obstacles',[]),key=lambda r:abs(wrap(p['angles'][1]+r['angle']-heading)),default=None)
        if (ray and abs(wrap(p['angles'][1]+ray['angle']-heading))<=17
            and any(ray.get('hit_id')==v.target['id'] and v.target.get('visible') is True
                and distance(p['eye'],v.target['p'])<108 for v in closed_doors)):
            # Fresh collision evidence puts a usable closed door directly in
            # the walking segment. Continuing that segment cannot open it.
            tasks.pop('route',None)
    # Exclude elective supply detours while actually being struck. Combat is
    # a separate question, so navigation need not disable the gun anymore.
    if threats:
        tasks={k:v for k,v in tasks.items() if v.kind not in ('loot','heal')}
    if getattr(memory,'navigation_review',False):
        # A bounded Astra approach instruction should reach its review point
        # before elective resupply. Keep immediate survival and doors usable.
        tasks={k:v for k,v in tasks.items() if v.kind not in ('loot','carry')
            or (v.kind=='loot' and ((critical_ammo_resupply(p) and v.target.get('type')=='weapon_ammo_spawn')
                or (p['health']+p.get('temp_health',0)<40 and 'first_aid_kit' in v.target.get('type',''))))}
    if 'heal' in tasks and not carry:
        # A carried medkit was repeatedly deferred for ordinary travel at
        # 53-58 health, then every healing attempt was interrupted in combat.
        # Use the already-checked quiet interval. A downed teammate remains a
        # meaningful alternative; hazards, Tanks and incapacitation took
        # precedence above, and the motor still interrupts an unsafe hold.
        return {k:v for k,v in tasks.items() if v.kind in ('heal','rescue')},waypoint
    if 'await_aid' in tasks:
        return {k:v for k,v in tasks.items() if k=='await_aid' or v.kind in ('rescue','boost')},waypoint
    if 'slot0' not in inv and not threats and not carry:
        primary_pickups=[v for v in tasks.values() if v.kind=='loot'
            and 'stronger primary gun' in v.description
            and v.target.get('visible') is not False and distance(p['p'],v.target['p'])<220]
        if primary_pickups:
            return {k:v for k,v in tasks.items() if v in primary_pickups or v.kind in ('rescue','boost')},waypoint
    if urgent_travel and critical_ammo_resupply(p) and not threats and not carry:
        refills={k:v for k,v in tasks.items() if v.kind=='loot' and v.target.get('type')=='weapon_ammo_spawn'}
        if refills:return refills,waypoint
    if not urgent_travel and not threats and not carry and not any(v.kind in ('rescue','use','deliver') for v in tasks.values()):
        # Continuing a long fuel route with an empty primary repeatedly won
        # over a visible ammo pile. Refill before leaving that observed supply;
        # immediate threats and ongoing carry/support tasks still take priority.
        low_primary=needs_ammo_resupply(p) or getattr(memory,'requested_ammo_refill',None) is not None
        refills=[v for v in tasks.values() if v.kind=='loot' and v.target.get('type')=='weapon_ammo_spawn'
            and distance(p['p'],v.target['p'])<800]
        kits=[v for v in tasks.values() if v.kind=='loot' and 'first_aid_kit' in v.target.get('type','')
            and distance(p['p'],v.target['p'])<600]
        if kits:
            return {k:v for k,v in tasks.items() if v in kits or (low_primary and v in refills) or v.kind in ('heal','boost')},waypoint
        if low_primary and refills:
            return {k:v for k,v in tasks.items() if v in refills or v.kind in ('heal','boost')},waypoint
        upgrades=[v for v in tasks.values() if v.kind=='loot' and 'stronger primary gun' in v.description and distance(p['p'],v.target['p'])<180]
        if upgrades:
            tasks.pop('route',None);tasks.pop('hold',None)
    return tasks,waypoint

def packet(state,nav,memory):
    tasks,waypoint=tasks_for(state,nav,memory)
    data=perceive(state,memory,waypoint)
    data['available_tasks']={k:{'description':v.description,'kind':v.kind,'mission_critical':v.critical} for k,v in tasks.items()}
    targets={'none':'No current enemy needs gunfire; do not disturb a calm Witch or shoot through a teammate.'}
    permitted_targets={e['id'] for e in state['enemies'] if engageable(state,e)}
    for e in data['threats']:
        if e['calm_witch'] or e['id'] not in permitted_targets:continue
        targets[f'enemy_{e["id"]}']=f"Engage the {e['kind']}: {e['range']}, {'threatening a pinned teammate' if e['pinned_teammate_nearby'] else 'visible threat'}, {'blocked by teammate' if e['friendly_blocks_shot'] else 'clear shot'} ."
        if e.get('frontal_armor'):
            targets[f'enemy_{e["id"]}']+=' Frontal riot armor: shove when close to expose its back or move past it. Do not waste frontal gunfire.'
    questions={
      'task':{'type':'choice','instructions':'Choose only the movement, support, or mission task. Combat is decided independently and can happen during travel. Prioritize leaving hazards, rescuing the team, required mission interactions, needed supplies, then travel. Hold only for a real waiting or defense need. Continue a useful unfinished task; change it when conditions demand.','criteria':{k:v.description for k,v in tasks.items()}},
      'target':{'type':'choice','instructions':'Which current enemy most urgently needs engagement? Prioritize a special infected holding a teammate or about to pin us, then an enemy within striking distance, then another active threat. A calm Witch is not a target. Choose none only when no threat needs combat. Do not do distance arithmetic; range and obstruction are already supplied.','criteria':targets},
      'combat':{'type':'choice','instructions':'Choose the immediate combat technique from the current situation, independently of travel. Shooting can continue while moving. Use the named range categories and ammunition facts.','criteria':{
          'fire':'A threatening enemy is visible and firearm ammunition is available; aim and fire, with code preventing friendly fire.',
          'shove':'A shovable enemy is touching us, especially a Hunter, Jockey, Smoker or Boomer. Push it away before firing. Tanks and Chargers cannot be shoved away.',
          'reload':'The current firearm is empty or nearly empty, reserve ammo is available or unknown, and no enemy is touching us. Reload even when no enemies are visible.',
          'hold':'No active enemy needs combat AND the gun does not currently need reloading; or an already ongoing climb, heal, revive or delivery requires uninterrupted input.'}}
    }
    # Deterministic feasibility: no target means no gunfire. This also prevents
    # independently answered questions from producing contradictory actions.
    combat=questions['combat']['criteria'];ammo=data['self']['ammo']
    if not data['combat_facts']['active_enemy_visible']:
        combat.pop('fire');combat.pop('shove')
    elif state['player']['incap'] or not any(e['range']=='touching' and e['type'] not in (6,7,8) for e in data['threats']):
        combat.pop('shove')
    if ammo['reserve']==0 or not data['self']['holding_firearm'] or (ammo['max_clip']>0 and ammo['clip']>=ammo['max_clip']):combat.pop('reload')
    return data,questions,tasks

def compose(result,tasks,state):
    answers=result['answers'];task=tasks[answers['task']['choice']]
    target=answers['target']['choice'];target_id=int(target[6:]) if target.startswith('enemy_') else None
    # These are already constrained ordinary, reversible gameplay actions.
    # Close-choice confidence stopped valid pickup/pour attempts before their
    # outcome could be observed. Actual failures and stalled progress, rather
    # than a confidence score alone, now trigger the controller's handoff.
    return {'task':task,'target_id':target_id,'combat':answers['combat']['choice'],
            'needs_review':False,'confidence':{k:v['confidence'] for k,v in answers.items()},
            'map':state['map'],'round_id':state.get('round_id'),'observed_t':state['t']}
