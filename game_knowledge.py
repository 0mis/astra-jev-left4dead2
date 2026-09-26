"""Original gameplay notes derived from installed Valve maps and instructor data.

Names identify live affordances, never instructions to fire entity inputs.
Every action still uses the ordinary player controls and checks its outcome.
"""
ENEMIES={0:'common infected',1:'Smoker',2:'Boomer',3:'Hunter',4:'Spitter',5:'Jockey',6:'Charger',7:'Witch',8:'Tank'}
CARRY_ITEMS={'weapon_cola_bottles','weapon_gascan'}
CHAPTERS={
 'c1m1_hotel':'Collect supplies, descend in the elevator, escape the burning lobby, and close the safe-room door.',
 'c1m2_streets':'Cross the streets, equip at the gun shop, collect the cola from the store and deliver it to Whitaker. After he clears the tanker, reach the safe room.',
 'c1m3_mall':'Traverse the mall. If the alarm starts, keep moving to its shutoff switch while fighting immediate blockers, then reach the safe room.',
 'c1m4_atrium':'Ride the elevator with the team. Collect and pour fuel into the car until the escape becomes available; verify the finale ending.',
 'c2m1_highway':'Follow the highway and motel route, stay together on drops, and reach the safe room.',
 'c2m2_fairgrounds':'Open the carousel gate, cross the event route, stop the carousel, and reach the safe room.',
 'c2m3_coaster':'Start the coaster, run its route while clearing blockers, shut down the alarm, and reach the safe room.',
 'c2m4_barns':'Reach and open the concert gates. Push through the horde to the safe room; do not wait for an endless horde to disappear.',
 'c2m5_concert':'Prepare supplies, activate stage lights and the finale microphone, survive the waves and Tanks, then board the rescue helicopter.',
 'c3m1_plankcountry':'Call the ferry, defend while it arrives, board with the team and operate its travel button. Continue to the safe room.',
 'c3m2_swamp':'Prepare health and gather the team before opening the crashed-plane exit. Defend together during its horde, then follow the swamp route to the safe room.',
 'c3m3_shantytown':'Lower the bridge, cover the team while it moves, then cross and reach the safe room.',
 'c3m4_plantation':'Contact rescue and confirm readiness at the gate radio. Defend against the waves and Tanks, then board the rescue boat.',
 'c4m1_milltown_a':'Travel inland toward the fuel station. Conserve supplies for the return trip and reach the safe room.',
 'c4m2_sugarmill_a':'Avoid disturbing calm Witches, call and ride the elevator with the team, and reach the gas station safe room.',
 'c4m3_sugarmill_b':'Return through the sugar mill in the storm, use the elevator normally, avoid Witches, and reach the safe room.',
 'c4m4_milltown_b':'Retrace the flooded town route toward the waterfront. Stay with the team when visibility is poor.',
 'c4m5_milltown_escape':'Call the rescue boat at the radio, defend from a position with supplies and escape routes, then board when ready.',
 'c5m1_waterfront':'Traverse the waterfront and reach the safe room without detouring to optional minigames.',
 'c5m2_park':'Cross the park and evacuation facility. During the alarm event, reach the shutoff tower and switch, then proceed through the opened exit.',
 'c5m3_cemetery':'Cross the cemetery and streets, avoid triggering car alarms, and reach the safe room.',
 'c5m4_quarter':'Activate the tractor to clear the route, survive the event, and continue to the safe room.',
 'c5m5_bridge':'Answer the bridge radio and confirm the finale, cross the bridge while clearing immediate blockers, then board the rescue helicopter.'
}
BUTTONS={
 'c1m1_hotel':{'elevator_button':'start elevator','elevator_door_button1':'open elevator exit'},
 'c1m2_streets':{'gunshop_door_button':'start gun-shop route'},
 'c1m3_mall':{'#320879':'shut off mall alarm'},
 'c1m4_atrium':{'button_elev_3rdfloor':'start elevator','pour_target':'pour fuel'},
 'c2m2_fairgrounds':{'carousel_gate_button':'open carousel gate','carousel_button':'stop carousel'},
 'c2m3_coaster':{'minifinale_button':'start coaster event','finale_alarm_stop_button':'stop coaster alarm'},
 'c2m4_barns':{'minifinale_gates_button':'open concert gates'},
 'c2m5_concert':{'stage_lights_button':'activate stage lights','stage_escape_button':'start rescue finale'},
 'c3m1_plankcountry':{'ferry_button':'call ferry','ferry_tram_button':'cross on ferry'},
 'c3m2_swamp':{'cabin_door_button':'open plane exit'},
 'c3m3_shantytown':{'bridge_button':'lower bridge'},
 'c3m4_plantation':{'escape_gate_button':'contact rescue','escape_gate_triggerfinale':'confirm rescue finale'},
 'c4m2_sugarmill_a':{'button_callelevator':'call elevator','button_inelevator':'ride elevator'},
 'c4m3_sugarmill_b':{'button_inelevator':'ride elevator'},
 'c4m5_milltown_escape':{'radio_button':'call rescue boat','radio':'confirm rescue finale'},
 'c5m2_park':{'finale_alarm_stop_button':'stop evacuation alarm'},
 'c5m4_quarter':{'tractor_button':'clear route with tractor'},
 'c5m5_bridge':{'radio_fake_button':'answer bridge radio','finale':'start bridge crossing'}
}
HOLDOUTS={'c2m5_concert','c3m4_plantation','c4m5_milltown_escape'}
FINALES=HOLDOUTS|{'c1m4_atrium','c5m5_bridge'}
TIER2=('rifle','autoshotgun','shotgun_spas','hunting_rifle','sniper')

# Installed Valve entity outputs explicitly remove these controls on OnPressed.
# Removal is a control outcome, never evidence of chapter/finale completion.
SELF_REMOVING_BUTTONS={
 'c1m1_hotel':{'elevator_button','elevator_door_button1'},
 'c2m2_fairgrounds':{'carousel_button','carousel_gate_button'},
 'c2m3_coaster':{'minifinale_button','finale_alarm_stop_button'},
 'c2m5_concert':{'stage_lights_button'},
 'c3m1_plankcountry':{'ferry_button','ferry_tram_button'},
 'c3m2_swamp':{'cabin_door_button'},
 'c4m2_sugarmill_a':{'button_inelevator'},
 'c4m3_sugarmill_b':{'button_inelevator'},
 'c5m2_park':{'finale_alarm_stop_button'},
 'c5m4_quarter':{'tractor_button'},
 'c5m5_bridge':{'radio_fake_button'},
}

def consumed_control(state,item):
    import math
    if (item.get('type')!='func_button' or item.get('name') not in SELF_REMOVING_BUTTONS.get(state['map'],set())
        or 'interactables' not in state or math.dist(state['player']['p'],item['p'])>700):return False
    # The observer enumerates all controls within900units, even occluded ones.
    # A missed sightline or leaving its sensor radius is not consumption.
    return not any(e.get('id')==item['id'] or e.get('name')==item['name']
        for e in state['interactables']+state.get('items',[]))

def firearm(name):
    return bool(name) and any(x in name for x in ('pistol','smg','shotgun','rifle','sniper','grenade_launcher'))

def role_for(state,item):
    name=item.get('name','');kind=item.get('type','');model=item.get('model','').lower()
    role=BUTTONS.get(state['map'],{}).get(name) or BUTTONS.get(state['map'],{}).get('#'+str(item.get('hammerid')))
    if role:return role
    if kind=='point_prop_use_target':
        if state['map']=='c1m2_streets':return 'deliver cola'
        if state['map']=='c1m4_atrium':return 'pour fuel'
    if state['map']=='c1m2_streets' and 'cola' in model:return 'collect cola'
    if state['map']=='c1m4_atrium' and ('gascan' in kind or 'gascan' in model):return 'collect fuel'
    if 'door' in kind:return 'door'
    return None

def ferry_stage(state):
    """Read the moving boat's actual controls, never route across empty water."""
    if state['map']!='c3m1_plankcountry':return None
    controls={e.get('name'):e for e in state.get('interactables',[])}
    if 'ferry_button' in controls:return None
    button=controls.get('ferry_tram_button');p=state['player']['p']
    if button:
        x=button['p'][0]
        if p[0]<-5380 and x>-5240:return 'arriving'
        if x<=-5240:
            aboard=(abs(p[0]-x)<95 and abs(p[1]-6064)<110)
            if not aboard:return 'boarding'
            return 'gathering' if button.get('locked') else 'operate'
    # The ordinary departure Use removes the button. Entrance doors stay
    # parented to the boat, so their live position still locates its deck.
    door=controls.get('ferry_door_right_entrance')
    if door and not button:
        x=door['p'][0]+113
        if -5255<=x<-4500 and abs(p[0]-x)<155 and abs(p[1]-6064)<140:return 'riding'
    return None

def elevator_stage(state):
    """Use the observed lift, not elapsed time or its static nav polygon."""
    if state['map'] not in ('c4m2_sugarmill_a','c4m3_sugarmill_b'):return None
    controls={e.get('name'):e for e in state.get('interactables',[])}
    lift=controls.get('elevator');button=controls.get('button_inelevator')
    if not lift or lift.get('type')!='func_elevator':return None
    p=state['player']['p'];q=lift['p'];z=q[2]
    down=state['map']=='c4m2_sugarmill_a'
    arrived=610<=z<=625 if down else 135<=z<=145
    destination=135<=z<=145 if down else 610<=z<=625
    stopped='velocity' in lift and abs(lift['velocity'][2])<.1
    aboard=abs(p[0]-q[0])<48 and abs(p[1]-q[1])<48 and 0<=p[2]-z<=25
    used=state.get('metrics',{}).get('last_use') or {}
    called=any(e.get('kind')=='sugarmill_elevator_called' for e in state.get('observed_mission_events',[]))
    called=called or (controls.get('button_callelevator',{}).get('id')==used.get('target') and used.get('target') is not None)
    if button:
        if not (arrived and stopped):return 'lift_arriving' if down and called else None
        if not aboard:return 'lift_boarding'
        return 'lift_gathering' if button.get('locked') else 'lift_operate'
    # The installed ordinary inside Use removes its button. Hold aboard
    # through the departure delay and movement, then release at the far floor.
    if aboard and not (destination and stopped):return 'lift_riding'
    return None

def mission(state):
    events={e['kind'] for e in state.get('events',[])+state.get('metrics',{}).get('recent',[])}
    events.update(k for k,v in state.get('metrics',{}).get('flags',{}).items() if v)
    events.update(e['kind'] for e in state.get('metrics',{}).get('hints',[]))
    phase='travel'
    transport=ferry_stage(state) or elevator_stage(state)
    if transport in ('arriving','gathering','riding','lift_arriving','lift_gathering','lift_riding'):phase='defend'
    event_kind,defense_seconds={
        'c3m2_swamp':('plane_exit_opened',60),
        'c3m3_shantytown':('shanty_bridge_lowering',16),
        'c5m2_park':('park_alarm_stopped',24),
        'c5m4_quarter':('quarter_tractor_started',66),
    }.get(state['map'],(None,0))
    starts=[e['t'] for e in state.get('observed_mission_events',[])
        if event_kind and e.get('kind')==event_kind]
    if starts and 0<=state['t']-max(starts)<defense_seconds:
        # Bounded tactical holds, not claims that a horde or chapter ended.
        # The installed bridge explicitly delays its nav-unblock by12s;
        # allow a short margin before resuming an ordinary crossing.
        # Keep independent firing, healing and emergency evasion available.
        phase='defend'
    bridge_starts=[e['t'] for e in state.get('events',[]) if e['kind']=='gauntlet_finale_start']
    used=state.get('metrics',{}).get('last_use') or {}
    bridge_confirmation_pending=(state['map']=='c5m5_bridge' and not bridge_starts
        and used.get('target') is not None and 0<=state['t']-used.get('t',-1000)<32
        and any(e.get('id')==used['target'] and e.get('type')=='trigger_finale'
            and e.get('name')=='finale' for e in state.get('interactables',[])))
    if bridge_confirmation_pending:
        # The installed switch has UseDelay30. An accepted ordinary Use is
        # followed by that delay before gauntlet_finale_start, not a failed
        # interaction to repeat. Keep a bound in case the event never arrives.
        phase='defend'
    if state['map']=='c5m5_bridge' and bridge_starts and 0<=state['t']-max(bridge_starts)<30:
        # Installed drawbridge travels267units at16units/s, then opens its
        # gate over about5s. Defend through that bounded mechanical interval.
        phase='defend'
    if state.get('exit_checkpoint_occupied') and state['map'] not in FINALES:phase='safe_room'
    if state['map'] in HOLDOUTS and 'finale_start' in events:phase='defend'
    # Atrium emitted finale_escape_start during a Tank wave at HUD7/13.
    # That generic stage event is not evidence that its car is fueled.
    if 'finale_vehicle_ready' in events or ('finale_escape_start' in events and state['map']!='c1m4_atrium'):phase='escape'
    if state['map']=='c4m5_milltown_escape' and phase=='escape' and 'finale_vehicle_ready' not in events:
        # The boat travels in after escape_start. Its actual arrival relay
        # enables the deck and boarding zone; don't walk onto an absent deck.
        phase='defend'
    if state['map']=='c2m5_concert' and phase=='escape':
        arrival=[e['t'] for e in state.get('metrics',{}).get('recent',[])
            if e['kind']=='finale_escape_start']
        # Both installed helicopter relays enable their boarding collision at
        # 17 seconds. The left trigger enables earlier, before landing.
        # This is a bounded defense interval, never an asserted rescue win.
        if arrival and 0<=state['t']-max(arrival)<18:phase='defend'
    return {'map':state['map'],'phase':phase,'transport':transport,'objective':CHAPTERS.get(state['map'],'Observe the current chapter objective and reach its verified exit.'),
            'bridge_confirmation_pending':bridge_confirmation_pending,
            'gauntlet_active':'gauntlet_finale_start' in events and phase!='defend',
            'finale_started':bool(events & {'finale_start','gauntlet_finale_start'}),
            'carry_complete':(state['map']=='c1m2_streets' and 'explain_store_item_stop' in events) or (state['map']=='c1m4_atrium' and phase=='escape'),
            'observed_hints':[e['kind'] for e in state.get('metrics',{}).get('hints',[])[-8:]],
            'carry_objective':'cola' if state['map']=='c1m2_streets' else 'fuel' if state['map']=='c1m4_atrium' else None}
