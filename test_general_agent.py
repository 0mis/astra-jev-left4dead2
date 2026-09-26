"""Regression scenarios for observed failures and consequential control boundaries."""
import copy
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_state import EvidenceMemory,perceive,friendly_blocked
from agent_policy import packet,Task,compose
from agent_motor import SkillMotor,movement_keys,CombatFocus
from agent_controller import decision_fresh,usable_decision,progress_reason,CombatMonitor,wait_for_landing,wait_for_elevator,completed_request
from navigation import Navigation,active_recoveries,NoRouteError,distance,reviewed_links
from game_knowledge import CHAPTERS,role_for,mission
from mission_planner import valid_directive
from vision_observer import accepted_facts,validate_facts
from local_motion import MotionRecovery

def state():
    return {'version':1,'seq':1,'t':100.,'map':'c1m2_streets','outcome':'active','events':[],
        'exit_checkpoint_occupied':False,'nav_ready':True,'nearby_nav':[],
        'player':{'id':1,'p':[0,0,0],'eye':[0,0,64],'angles':[0,0,0],'velocity':[0,0,0],
            'health':73,'temp_health':0,'weapon':'weapon_pumpshotgun','clip':3,'inventory':{'slot0':'weapon_pumpshotgun','slot1':'weapon_pistol','slot3':'weapon_first_aid_kit'},
            'weapons':{'slot0':{'type':'weapon_pumpshotgun','clip':3,'max_clip':8,'reserve':55,'next_attack':0,'reloading':False}},
            'next_attack':0,'area':1,'flow':100,'nearest_area':False,'dead':False,'pinned':False,'incap':False,'ledge':False,'immobilized':False,
            'on_fire':False,'area_damaging':False,'on_ladder':False},
        'enemies':[],'teammates':[],'items':[],'interactables':[],'metrics':{'shots':0,'heals':0,'pours':0,'last_use':None,'recent':[]}}


class RunningFinaleTests(unittest.TestCase):
    def test_critical_medical_pickup_gets_a_bounded_approach_without_wider_detours(self):
        from agent_policy import tasks_for
        s=state();s['map']='c5m5_bridge';s['events']=[{'kind':'gauntlet_finale_start','t':10}]
        s['player']['health']=28;s['player']['inventory'].pop('slot3')
        s['items']=[{'id':327,'type':'weapon_first_aid_kit_spawn','p':[180,0,40],'visible':True},
            {'id':194,'type':'weapon_adrenaline_spawn','p':[170,0,36],'visible':True}]
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory())
        self.assertEqual(tasks['loot_327'].timeout,18);self.assertTrue(tasks['loot_327'].critical)
        self.assertEqual(tasks['loot_194'].timeout,18)
        s['items'][0]['p']=[240,0,40];s['items'][1]['visible']=False
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory())
        self.assertNotIn('loot_327',tasks);self.assertNotIn('loot_194',tasks)

    def test_evacuation_does_not_offer_unsafe_or_repeated_interrupted_revives(self):
        from agent_policy import tasks_for
        s=state();s['map']='c5m5_bridge';s['events']=[{'kind':'finale_vehicle_ready','t':90}]
        s['teammates']=[{'id':2,'p':[100,0,0],'health':150,'incap':True,'dead':False,'pinned':False}]
        mem=EvidenceMemory();tasks,_=tasks_for(s,FlatNav(),mem)
        self.assertIn('rescue_2',tasks)
        s['player']['health']=29;s['player']['inventory'].pop('slot3')
        tasks,_=tasks_for(s,FlatNav(),mem)
        self.assertNotIn('rescue_2',tasks);self.assertIn('route',tasks)
        s['player']['health']=73;s['teammates'][0]['p']=[200,0,0]
        self.assertNotIn('rescue_2',tasks_for(s,FlatNav(),mem)[0])
        s['teammates'][0]['p']=[100,0,0]
        s['enemies']=[{'id':8,'type':0,'p':[100,0,64],'health':50}]
        self.assertNotIn('rescue_2',tasks_for(s,FlatNav(),mem)[0]);s['enemies']=[]
        for t in (95,98):mem.outcome('rescue_2','interrupted',t,'Attacker interrupted support.')
        self.assertNotIn('rescue_2',tasks_for(s,FlatNav(),mem)[0])
        s['t']=129;self.assertIn('rescue_2',tasks_for(s,FlatNav(),mem)[0])

    def test_evacuation_rescue_rule_does_not_remove_normal_campaign_aid(self):
        from agent_policy import tasks_for
        s=state();s['player']['health']=29;s['player']['inventory'].pop('slot3')
        s['teammates']=[{'id':2,'p':[400,0,0],'health':150,'incap':True,'dead':False,'pinned':False}]
        self.assertIn('rescue_2',tasks_for(s,FlatNav(),EvidenceMemory())[0])

    def test_missing_primary_picks_visible_gun_before_departing_or_starting_event(self):
        from agent_policy import tasks_for
        s=state();s['player']['inventory'].pop('slot0');s['player']['weapons'].pop('slot0')
        s['player'].update(weapon='weapon_pistol',clip=15)
        gun={'id':42,'type':'weapon_spawn','model':'models/w_models/weapons/w_rifle_ak47.mdl','p':[100,0,32],'visible':True}
        s['items']=[gun]
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory());self.assertEqual(set(tasks),{'loot_42'})
        s['enemies']=[{'id':88,'type':8,'p':[180,0,32],'health':3000}]
        self.assertEqual(set(tasks_for(s,FlatNav(),EvidenceMemory())[0]),{'evade'})

    def test_missing_primary_search_survives_bridge_frontier_and_ends_on_equipping(self):
        from mission_planner import MissionPlanner
        s=state();s.update(map='c5m5_bridge',round_id='fresh-recovery',events=[{'kind':'gauntlet_finale_start','t':10}])
        s['player']['inventory'].pop('slot0');s['player']['weapons'].pop('slot0');s['player'].update(weapon='weapon_pistol',clip=15)
        n=FlatNav();n.map=s['map'];mem=EvidenceMemory()
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)),patch('mission_planner.observed_rescue_frontier',return_value=3):
            d={'map':s['map'],'round_id':s['round_id'],'version':'supply-search','created_game_t':99,'expires_game_t':200,
                'goal_area':1,'missing_primary_search':True,'public_instruction':'Reacquire an actual gun at the reviewed supply table.'}
            (Path(directory)/'planner-directive.json').write_text(json.dumps(d))
            planner=MissionPlanner(n,mem);planner.update(s)
            self.assertEqual(n.goal,1);self.assertTrue(mem.navigation_goal['critical_supply'])
            s['player']['inventory']['slot0']='weapon_rifle_ak47';s['t']+=1;planner.update(s)
            self.assertEqual(n.goal,3);self.assertFalse(mem.navigation_goal['critical_supply'])
            s['player']['inventory'].pop('slot0');s['t']=201;planner.update(s);self.assertEqual(n.goal,3)

    def test_evacuation_allows_only_nearby_live_critical_ammo_then_resumes_boarding(self):
        from mission_planner import MissionPlanner
        from agent_policy import tasks_for
        s=state();s.update(map='c5m5_bridge',round_id='evac-ammo',events=[{'kind':'finale_vehicle_ready','t':90}])
        s['player']['weapons']['slot0'].update(clip=0,reserve=0)
        ammo={'id':635,'type':'weapon_ammo_spawn','p':[650,0,36],'visible':True}
        s['items']=[ammo];s['interactables']=[{'id':653,'type':'trigger_multiple','name':'trigger_heli','disabled':False,'p':[900,0,0]}]
        n=FlatNav();n.map=s['map'];mem=EvidenceMemory();mem.remembered_items={635:{'item':ammo}}
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)),patch('mission_planner.observed_rescue_frontier',return_value=3):
            planner=MissionPlanner(n,mem);planner.update(s)
            self.assertEqual(n.goal,2);self.assertTrue(mem.navigation_goal['critical_supply'])
            self.assertNotIn('loot_635',tasks_for(s,n,mem)[0])
            s['player'].update(p=[500,0,0],eye=[500,0,64]);s['t']+=1;planner.update(s)
            self.assertIn('loot_635',tasks_for(s,n,mem)[0])
            s['player']['weapons']['slot0']['reserve']=100;s['t']+=1;planner.update(s)
            self.assertEqual(n.goal,3);self.assertTrue(mem.navigation_goal['rescue'])
            s['player']['weapons']['slot0']['reserve']=0;s['player'].update(p=[-100,0,0],eye=[-100,0,64]);s['t']+=1;planner.update(s)
            self.assertEqual(n.goal,3)
            s['player'].update(p=[0,0,0],eye=[0,0,64]);s['items']=[];s['t']+=1;planner.update(s)
            self.assertEqual(n.goal,3)

    def test_bridge_accepted_switch_waits_for_actual_delayed_start(self):
        from agent_policy import tasks_for
        s=state();s['map']='c5m5_bridge'
        switch={'id':29,'type':'trigger_finale','name':'finale','p':[34,-12,57],'visible':True}
        s['interactables']=[switch]
        s['metrics']['last_use']={'target':29,'t':73.3}
        self.assertTrue(mission(s)['bridge_confirmation_pending'])
        self.assertFalse(mission(s)['finale_started'])
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory())
        self.assertNotIn('route',tasks);self.assertNotIn('use_29',tasks)
        s['t']=105.4
        self.assertFalse(mission(s)['bridge_confirmation_pending'])
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory());self.assertIn('use_29',tasks)
        s['events']=[{'kind':'gauntlet_finale_start','t':103.3}]
        self.assertTrue(mission(s)['finale_started']);self.assertFalse(mission(s)['bridge_confirmation_pending'])
        s['events']=[];s['t']=100;s['metrics']['last_use']['target']=85
        self.assertFalse(mission(s)['bridge_confirmation_pending'])

    def test_bridge_frontier_preserves_empty_primary_refill_route_until_refilled(self):
        from mission_planner import MissionPlanner
        from agent_policy import tasks_for
        s=state();s.update(map='c5m5_bridge',round_id='bridge-ammo-test',events=[{'kind':'gauntlet_finale_start','t':10}])
        n=FlatNav();n.map=s['map'];mem=EvidenceMemory()
        s['player']['weapons']['slot0'].update(clip=0,reserve=0)
        ammo={'id':627,'type':'weapon_ammo_spawn','p':[686,0,36],'visible':True}
        s['items']=[ammo];mem.remembered_items={627:{'item':ammo}}
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)),patch('mission_planner.observed_rescue_frontier',return_value=3):
            planner=MissionPlanner(n,mem);planner.update(s)
            self.assertEqual(n.goal,2);self.assertTrue(mem.navigation_goal['critical_supply'])
            tasks,_=tasks_for(s,n,mem)
            self.assertIn('route',tasks);self.assertTrue(tasks['route'].critical)
            self.assertNotIn('loot_627',tasks)
            s['player'].update(p=[520,0,0],eye=[520,0,64]);s['t']+=1
            planner.update(s);tasks,_=tasks_for(s,n,mem)
            self.assertIn('loot_627',tasks);self.assertEqual(tasks['loot_627'].timeout,12)
            s['player']['weapons']['slot0']['reserve']=100;s['t']+=1
            planner.update(s)
            self.assertEqual(n.goal,3);self.assertFalse(mem.navigation_goal['critical_supply'])

    def test_critical_ammo_refill_remains_available_during_running_finale(self):
        from agent_policy import tasks_for
        s=state();s['map']='c5m5_bridge';s['events']=[{'kind':'gauntlet_finale_start','t':10}]
        p=s['player'];p['weapon']='weapon_rifle_ak47';p['inventory']['slot0']=p['weapon']
        p['weapons']['slot0'].update(type=p['weapon'],clip=14,reserve=0,max_clip=40)
        s['items']=[{'id':642,'type':'weapon_ammo_spawn','p':[500,0,0],'visible':True}]
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory())
        self.assertEqual(set(tasks),{'loot_642'});self.assertEqual(tasks['loot_642'].timeout,12)
        p['weapons']['slot0']['reserve']=200
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory())
        self.assertNotIn('loot_642',tasks);self.assertIn('route',tasks)

    def test_running_begins_after_actual_bridge_lowering_wait(self):
        s=state();s['map']='c5m5_bridge'
        self.assertFalse(mission(s)['gauntlet_active'])
        s['events']=[{'kind':'gauntlet_finale_start','t':90}]
        self.assertFalse(mission(s)['gauntlet_active'])
        s['t']=121;self.assertTrue(mission(s)['gauntlet_active'])
        n=FlatNav();motor=SkillMotor(n,EvidenceMemory())
        step={'p':[200,0,0],'area':2,'attr':0,'link_type':'walk','align_portal':True}
        with patch.object(n,'waypoint',return_value=step):
            action=motor.tick(s,choice(target=None,combat='hold'))
        self.assertIn('w',action['keys']);self.assertNotIn('shift',action['keys'])

    def test_running_finale_keeps_nearby_pickups_but_excludes_distant_or_hidden_detours(self):
        from agent_policy import tasks_for
        s=state();s['map']='c5m5_bridge';s['events']=[{'kind':'gauntlet_finale_start','t':10}]
        s['player']['inventory'].pop('slot3')
        s['items']=[
            {'id':385,'type':'weapon_first_aid_kit_spawn','p':[532,0,0],'visible':True},
            {'id':386,'type':'weapon_first_aid_kit_spawn','p':[80,0,0],'visible':True},
            {'id':387,'type':'weapon_pain_pills_spawn','p':[90,0,0],'visible':False}]
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory())
        self.assertNotIn('loot_385',tasks);self.assertNotIn('loot_387',tasks)
        self.assertEqual(tasks['loot_386'].timeout,6);self.assertIn('route',tasks)
        s['events']=[]
        tasks,_=tasks_for(s,FlatNav(),EvidenceMemory())
        self.assertIn('loot_385',tasks);self.assertEqual(tasks['loot_386'].timeout,18)


class LadderLandingTests(unittest.TestCase):
    def test_attached_tanker_ladder_uses_upward_view_then_landing_view(self):
        n=Navigation.__new__(Navigation);n.goal=2;n.path=[1,2]
        n.areas={1:{'p':[3652,6446,456]},2:{'p':[3730,6446,584]}}
        ladder={'id':87,'bottom_area':1,'top_area':2,'dir':3,
            'bottom':[3683.9688,6446.1704,456.8898],'top':[3683.9688,6446.1704,583.3223]}
        n.active_ladder=(ladder,True)
        s={'player':{'p':[3665.3833,6442.0781,515.5317],'area':1,'flow':17107,'on_ladder':True}}
        step=n.ladder_waypoint(s)
        self.assertEqual(step['phase'],'climb');self.assertLessEqual(step['pitch'],-35)
        self.assertEqual(step['face_yaw'],0)
        s['player']['p'][2]=582.1979
        step=n.ladder_waypoint(s)
        self.assertEqual(step['phase'],'dismount');self.assertLess(abs(step['pitch']),10)
        n.active_ladder=(ladder,False);s['player']['p'][2]=515.5317
        self.assertGreater(n.ladder_waypoint(s)['pitch'],35)

    def test_escape_can_hop_small_native_curb_but_not_high_or_blocked_floor(self):
        n=Navigation.__new__(Navigation);n.goal=2;n.avoided=set();n.blocked=set();n.damaging=set();n.ladder_edges={}
        n.areas={
            1:{'id':1,'p':[0,0,0],'nw':[-25,-25,0],'se':[25,25,0],'flow':100,'adj':[2]},
            2:{'id':2,'p':[0,50,20],'nw':[-25,25,20],'se':[25,75,20],'flow':150,'adj':[]}}
        self.assertEqual(n.route(1,2,escaping=True),[1,2])
        n.blocked.add(2)
        with self.assertRaises(NoRouteError):n.route(1,2,escaping=True)
        n.blocked.clear();n.areas[2].update(p=[0,50,50],nw=[-25,25,50],se=[25,75,50])
        with self.assertRaises(NoRouteError):n.route(1,2,escaping=True)

    def test_comparable_walking_route_preferred_but_required_ladder_retained(self):
        n=Navigation.__new__(Navigation);n.goal=3;n.avoided=set();n.blocked=set();n.damaging=set()
        n.areas={
            1:{'id':1,'p':[0,0,120],'nw':[-10,-10,120],'se':[10,10,120],'flow':0,'adj':[2]},
            2:{'id':2,'p':[100,0,60],'nw':[10,-10,0],'se':[190,10,120],'flow':100,'adj':[3]},
            3:{'id':3,'p':[20,0,0],'nw':[10,-10,0],'se':[30,10,0],'flow':200,'adj':[]}}
        ladder={'id':266,'top':[0,0,120],'bottom':[20,0,0]}
        n.ladder_edges={(1,3):(ladder,False)}
        self.assertEqual(n.route(1,3),[1,2,3])
        n.areas[1]['adj']=[];self.assertEqual(n.route(1,3),[1,3])

    def test_missing_forward_landing_keeps_observed_side_exits(self):
        from navigation import ladder_connections
        ladder={'id':333,'bottom_area':1,'top_area':None,'top_areas':[2,3,999]}
        edges=ladder_connections([ladder],{1:{},2:{},3:{},4:{}})
        self.assertEqual(set(edges),{(1,2),(2,1),(1,3),(3,1)})
        self.assertEqual(edges[(1,3)][0]['top_area'],3)
        self.assertTrue(edges[(1,3)][1]);self.assertFalse(edges[(3,1)][1])
        self.assertIsNone(ladder['top_area'])

    def test_route_prefers_road_over_slightly_shorter_precision_ledge(self):
        n=Navigation.__new__(Navigation);n.goal=4;n.avoided=set();n.blocked=set();n.damaging=set();n.ladder_edges={}
        positions={1:[0,0,0],2:[100,0,0],3:[100,65,0],4:[200,0,0]}
        n.areas={i:{'id':i,'p':p,'nw':[p[0]-10,p[1]-10,0],
            'se':[p[0]+10,p[1]+10,0],'flow':i*100,'attr':4 if i==2 else 0,
            'adj':[2,3] if i==1 else [4] if i in (2,3) else []} for i,p in positions.items()}
        self.assertEqual(n.route(1,4),[1,3,4])
        self.assertEqual(n.route(1,4,escaping=True),[1,3,4])
        n.areas[1]['adj']=[2]
        self.assertEqual(n.route(1,4),[1,2,4])
        n.areas[1]['adj']=[2,3];n.areas[3]['nw'][2]=200;n.areas[3]['se'][2]=200;n.areas[3]['p'][2]=200
        self.assertEqual(n.route(1,4),[1,2,4])

    def test_no_invented_landing_when_engine_has_no_connection(self):
        from navigation import ladder_connections
        self.assertEqual(ladder_connections([{'bottom_area':1,'top_area':None,'top_areas':[]}],{1:{},2:{}}),{})
        edges=ladder_connections([{'bottom_area':1,'top_area':2}],{1:{},2:{}})
        self.assertEqual(set(edges),{(1,2),(2,1)})

class PlaneEventDefenseTests(unittest.TestCase):
    def test_bridge_lowering_wait_requires_actual_gauntlet_event(self):
        s=state();s['map']='c5m5_bridge'
        s['metrics']['last_use']={'target':29,'t':90}
        self.assertEqual(mission(s)['phase'],'travel')
        s['events']=[{'kind':'gauntlet_finale_start','t':94.2}]
        self.assertEqual(mission(s)['phase'],'defend')
        s['t']=125;self.assertEqual(mission(s)['phase'],'travel')

    def test_bridge_approaches_radio_then_switch_until_actual_finale_start(self):
        from mission_planner import MissionPlanner
        s=state();s.update(map='c5m5_bridge',round_id='bridge-1');nav=FlatNav();nav.map=s['map'];mem=EvidenceMemory()
        for a in nav.areas.values():a['flow']=100
        s['interactables']=[{'id':28,'type':'func_button','name':'radio_fake_button','p':[200,0,64],'disabled':False}]
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(nav,mem);planner.update(s);self.assertEqual(nav.goal,2)
            s['interactables']=[];planner.update(s);self.assertEqual(nav.goal,2)
            s['interactables']=[{'id':91,'type':'trigger_finale','name':'finale','p':[250,0,64],'disabled':False}]
            planner.update(s);self.assertEqual(nav.goal,2)
            s['events']=[{'kind':'finale_start','t':s['t']}];planner.update(s);self.assertEqual(nav.goal,3)

    def test_bridge_rescue_needs_actual_vehicle_ready_and_enabled_trigger(self):
        from mission_planner import MissionPlanner
        s=state();s.update(map='c5m5_bridge',round_id='bridge-1');nav=FlatNav();nav.map=s['map'];mem=EvidenceMemory()
        s['events']=[{'kind':'finale_start','t':20},{'kind':'finale_escape_start','t':90}]
        s['interactables']=[{'id':99,'type':'trigger_multiple','name':'trigger_heli','p':[900,0,60],'disabled':False}]
        for a in nav.areas.values():a['flow']=100
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(nav,mem);planner.update(s);self.assertFalse((mem.navigation_goal or {}).get('rescue'))
            s['events'].append({'kind':'finale_vehicle_ready','t':99});s['interactables'][0]['disabled']=True
            planner.update(s);self.assertIsNone(mem.navigation_goal)
            s['interactables'][0]['disabled']=False;planner.update(s)
            self.assertTrue(mem.navigation_goal['rescue']);self.assertEqual(s['outcome'],'active')

    def test_hard_rain_waits_for_actual_arrival_before_boat_route(self):
        from mission_planner import MissionPlanner
        s=state();s['map']='c4m5_milltown_escape'
        s['events']=[{'kind':'finale_start','t':20},{'kind':'finale_escape_start','t':90}]
        s['interactables']=[{'id':99,'name':'trigger_boat','type':'trigger_multiple','p':[900,0,60],'disabled':True}]
        self.assertEqual(mission(s)['phase'],'defend')
        mem=EvidenceMemory();nav=FlatNav();nav.map=s['map']
        for a in nav.areas.values():a['flow']=100
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(nav,mem);planner.update(s);self.assertIsNone(mem.navigation_goal)
            s['events'].append({'kind':'finale_vehicle_ready','t':99});s['interactables'][0]['disabled']=False
            planner.update(s)
            self.assertEqual(mission(s)['phase'],'escape');self.assertTrue(mem.navigation_goal['rescue'])
            self.assertEqual(s['outcome'],'active')

    def test_explicit_prefinale_refill_requires_matching_live_ammunition(self):
        s=state();s['player']['weapons']['slot0']['reserve']=100
        s['items']=[{'id':371,'type':'weapon_ammo_spawn','p':[80,0,30]}]
        mem=EvidenceMemory();nav=FlatNav()
        self.assertNotIn('loot_371',packet(s,nav,mem)[2])
        mem.requested_ammo_refill=371
        self.assertEqual(set(packet(s,nav,mem)[2]),{'loot_371'})
        s['items'][0]['id']=372
        self.assertNotIn('loot_372',packet(s,nav,mem)[2])

    def test_rescue_frontier_uses_reachable_observed_flow_and_advances(self):
        from mission_planner import observed_rescue_frontier
        s=state();nav=FlatNav()
        for a in nav.areas.values():a['flow']=100
        nav.areas[3]['flow']=-9999
        control={'p':[900,0,60]}
        self.assertEqual(observed_rescue_frontier(s,nav,control),2)
        nav.areas[3]['flow']=900
        self.assertEqual(observed_rescue_frontier(s,nav,control),3)
        with patch.object(nav,'routes_to',return_value={1:[1],2:[1,2]}):
            self.assertEqual(observed_rescue_frontier(s,nav,control),2)

    def test_rescue_override_requires_both_escape_phase_and_enabled_observed_zone(self):
        from mission_planner import MissionPlanner
        s=state();s['map']='c3m4_plantation';s['events']=[{'kind':'finale_start','t':90}]
        s['interactables']=[{'id':224,'type':'trigger_multiple','name':'escape_boat_trigger','p':[900,0,60],'disabled':True}]
        mem=EvidenceMemory();nav=FlatNav();nav.map=s['map']
        for a in nav.areas.values():a['flow']=100
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(nav,mem);planner.update(s)
            self.assertIsNone(mem.navigation_goal)
            s['events'].append({'kind':'finale_vehicle_ready','t':99});planner.update(s)
            self.assertIsNone(mem.navigation_goal)
            s['interactables'][0]['disabled']=False;planner.update(s)
            self.assertTrue(mem.navigation_goal['rescue']);self.assertEqual(nav.goal,3)

    def test_fresh_engine_flow_invalidates_route_cache_without_guessing_missing_flow(self):
        nav=Navigation.__new__(Navigation);nav.areas={1:{'flow':-9999},2:{'flow':0}}
        nav.damaging=set();nav.blocked=set();nav.cached_start=(1,None,False)
        s=state();s['nearby_nav']=[{'id':1,'flow':14500.,'blocked':False,'damaging':False},
            {'id':2,'blocked':False,'damaging':False}]
        nav.update(s)
        self.assertEqual(nav.areas[1]['flow'],14500.);self.assertEqual(nav.areas[2]['flow'],0)
        self.assertIsNone(nav.cached_start)
        s['nearby_nav'][0]['flow']=float('nan');nav.update(s)
        self.assertEqual(nav.areas[1]['flow'],14500.)

    def test_holdout_allows_confirmed_critical_resupply_but_keeps_tank_evasion(self):
        s=state();s['map']='c3m4_plantation';s['events']=[{'kind':'finale_start','t':90}]
        mem=EvidenceMemory();nav=FlatNav()
        self.assertNotIn('route',packet(s,nav,mem)[2])
        mem.navigation_goal={'critical_supply':True,'purpose':'Reach remembered ammunition.'}
        self.assertIn('route',packet(s,nav,mem)[2])
        s['enemies']=[enemy(9,kind=8,p=[200,0,64])]
        self.assertEqual(set(packet(s,nav,mem)[2]),{'evade'})

    def test_reached_visible_required_control_retires_approach_but_not_survival(self):
        s=state();s['map']='c3m4_plantation';mem=EvidenceMemory();nav=FlatNav()
        button={'id':550,'type':'func_button','name':'escape_gate_button','p':[80,0,64],
            'visible':True,'locked':False,'disabled':False}
        s['interactables']=[button]
        tasks=packet(s,nav,mem)[2]
        self.assertIn('use_550',tasks);self.assertNotIn('route',tasks)
        button['visible']=False
        self.assertIn('route',packet(s,nav,mem)[2])
        button['visible']=True;button['p'][0]=150
        self.assertIn('route',packet(s,nav,mem)[2])
        button['p'][0]=80;button['disabled']=True
        self.assertIn('route',packet(s,nav,mem)[2])
        button['disabled']=False;s['player']['health']=40
        self.assertEqual(set(packet(s,nav,mem)[2]),{'heal'})

    def test_known_physical_ladder_can_transit_without_inventing_a_floor(self):
        from agent_controller import known_ladder_transit
        s=state();s['player'].update(area=None,on_ladder=True,p=[2083.46,-708.03,344.87])
        nav=FlatNav();nav.active_ladder=({'bottom':[2083.58,-692.03,181.8],'top':[2083.58,-692.03,392.03]},True)
        self.assertTrue(known_ladder_transit(s,nav));self.assertIsNone(s['player']['area'])
        s['player']['p'][0]+=100;self.assertFalse(known_ladder_transit(s,nav))
        s['player']['p'][0]-=100;s['player']['on_ladder']=False
        self.assertFalse(known_ladder_transit(s,nav))
        s['player']['on_ladder']=True;nav.active_ladder=None
        self.assertFalse(known_ladder_transit(s,nav))

    def test_only_actual_named_control_use_starts_bounded_defense_across_handoffs(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            s=state();s.update(map='c3m2_swamp',round_id='plane-a')
            nav=FlatNav();nav.map=s['map'];mem=EvidenceMemory()
            button={'id':77,'type':'func_button','name':'cabin_door_button','p':[80,0,64]}
            s['interactables']=[button];mem.remembered_items={77:{'item':button}}
            planner=MissionPlanner(nav,mem);planner.update(s)
            self.assertEqual(mission(s)['phase'],'travel');self.assertEqual(planner.intent['id'],77)
            s['metrics']['last_use']={'target':76,'t':s['t']};planner.update(s)
            self.assertEqual(mission(s)['phase'],'travel')
            s['interactables']=[];mem.remembered_items={};s['t']+=1;planner.update(s)
            self.assertEqual(mission(s)['phase'],'travel')
            s['metrics']['last_use']={'target':77,'t':s['t']};planner.update(s)
            self.assertEqual(mission(s)['phase'],'defend')
            self.assertNotIn('route',packet(s,nav,mem)[2])
            resumed=MissionPlanner(nav,EvidenceMemory());s['t']+=10;resumed.update(s)
            self.assertEqual(mission(s)['phase'],'defend')
            s['t']+=50;resumed.update(s)
            self.assertEqual(mission(s)['phase'],'travel');self.assertEqual(s['outcome'],'active')
            s.update(round_id='plane-b');s['metrics']['last_use']=None
            restarted=MissionPlanner(nav,EvidenceMemory());restarted.update(s)
            self.assertEqual(s['observed_mission_events'],[])

    def test_plane_defense_still_allows_healing_and_emergency_evasion(self):
        s=state();s['map']='c3m2_swamp';s['player']['health']=25
        s['observed_mission_events']=[{'kind':'plane_exit_opened','t':s['t']-2}]
        mem=EvidenceMemory();nav=FlatNav()
        tasks=packet(s,nav,mem)[2]
        self.assertIn('heal',tasks);self.assertNotIn('route',tasks)
        s['player']['on_fire']=True
        self.assertEqual(set(packet(s,nav,mem)[2]),{'escape'})

    def test_bridge_use_waits_for_lowering_then_restores_travel_without_claiming_completion(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            s=state();s.update(map='c3m3_shantytown',round_id='bridge-a')
            nav=FlatNav();nav.map=s['map'];mem=EvidenceMemory()
            button={'id':41,'type':'func_button','name':'bridge_button','p':[80,0,64]}
            s['interactables']=[button];mem.remembered_items={41:{'item':button}}
            planner=MissionPlanner(nav,mem);planner.update(s)
            self.assertEqual(mission(s)['phase'],'travel')
            s['metrics']['last_use']={'target':41,'t':s['t']};s['interactables']=[dict(button,locked=True)]
            mem.remembered_items={};planner.update(s)
            for elapsed,expected in [(0,'defend'),(12,'defend'),(15.9,'defend'),(16,'travel')]:
                s['t']=100+elapsed;planner.update(s)
                self.assertEqual(mission(s)['phase'],expected)
                self.assertEqual('route' in packet(s,nav,mem)[2],expected=='travel')
            self.assertEqual(s['outcome'],'active')


class SugarmillElevatorTests(unittest.TestCase):
    def test_parish_mechanical_waits_require_actual_use_and_expire(self):
        for mapname,event,duration in [('c5m2_park','park_alarm_stopped',24),('c5m4_quarter','quarter_tractor_started',66)]:
            s=state();s['map']=mapname
            self.assertEqual(mission(s)['phase'],'travel')
            s['observed_mission_events']=[{'kind':event,'t':s['t']}]
            self.assertEqual(mission(s)['phase'],'defend')
            s['t']+=duration;self.assertEqual(mission(s)['phase'],'travel')

    def test_live_finale_start_prevents_repeating_persistent_radio_button(self):
        s=state();s['map']='c4m5_milltown_escape'
        s['interactables']=[{'id':48,'name':'radio_button','type':'func_button','p':[30,0,64],'visible':True,'locked':False}]
        mem=EvidenceMemory();mem.update(s)
        self.assertIn('use_48',packet(s,FlatNav(),mem)[2])
        s['events']=[{'kind':'finale_start','t':s['t']}]
        self.assertNotIn('use_48',packet(s,FlatNav(),mem)[2])

    def test_missing_nearest_lookup_only_resolves_inside_fresh_supported_floor(self):
        s=state();s['player'].update(area=None,nearest_area=True,p=[5,5,98])
        s['nearby_nav']=[{'id':1,'blocked':False,'damaging':False}]
        nav=FlatNav();nav.areas[1].update(nw=[0,0,100],se=[20,20,100])
        self.assertIsNotNone(Navigation.correct_player_area(nav,s));self.assertEqual(s['player']['area'],1)
        for change in ({'p':[21,5,98]},{'p':[5,5,80]},{'velocity':[0,0,-5]},{'pinned':True},{'nearest_area':False}):
            q=copy.deepcopy(s);q['player'].update(area=None,**change)
            self.assertIsNone(Navigation.correct_player_area(nav,q))
        s['player']['area']=None;s['nearby_nav'][0]['blocked']=True
        self.assertIsNone(Navigation.correct_player_area(nav,s))

    def test_forced_charger_carry_waits_for_nearby_team_with_deadline(self):
        from agent_controller import wait_for_pinned_rescue
        s=state();s['player'].update(pinned=True,area=None);s['teammates']=[bot()]
        self.assertTrue(wait_for_pinned_rescue(s,1));self.assertFalse(wait_for_pinned_rescue(s,15))
        s['teammates'][0]['dead']=True;self.assertFalse(wait_for_pinned_rescue(s,1))
        s['teammates']=[bot()];s['player']['pinned']=False
        self.assertFalse(wait_for_pinned_rescue(s,1))

    def scene(self,z=505,velocity=10):
        s=state();s['map']='c4m2_sugarmill_a';s['round_id']='lift-test'
        s['player']['p']=[-1422,-9467,624];s['player']['eye']=[-1422,-9467,686]
        s['metrics']['last_use']={'target':49,'t':90}
        s['interactables']=[
            {'id':49,'name':'button_callelevator','type':'func_button','p':[-1408,-9485,662],'locked':False},
            {'id':411,'name':'button_inelevator','type':'func_button','p':[-1477,-9594,z+31],'locked':True},
            {'id':443,'name':'elevator','type':'func_elevator','p':[-1478,-9551,z],'velocity':[0,0,velocity]}]
        return s

    def test_live_arrival_blocks_repeat_call_but_retains_healing(self):
        s=self.scene();s['player']['health']=30;mem=EvidenceMemory();mem.update(s)
        self.assertEqual(mission(s)['transport'],'lift_arriving')
        tasks=packet(s,FlatNav(),mem)[2]
        self.assertIn('heal',tasks);self.assertNotIn('route',tasks);self.assertNotIn('use_49',tasks)
        s['interactables'][2]['p'][2]=617
        self.assertEqual(mission(s)['transport'],'lift_arriving')
        del s['interactables'][2]['velocity']
        self.assertEqual(mission(s)['transport'],'lift_arriving')

    def test_actual_stop_then_team_unlock_precedes_inside_use(self):
        s=self.scene(617,0)
        self.assertEqual(mission(s)['transport'],'lift_boarding')
        s['player']['p']=[-1478,-9551,632];s['player']['eye']=[-1478,-9551,696]
        self.assertEqual(mission(s)['transport'],'lift_gathering')
        s['interactables'][1]['locked']=False
        self.assertEqual(mission(s)['transport'],'lift_operate')
        mem=EvidenceMemory();mem.update(s);tasks=packet(s,FlatNav(),mem)[2]
        self.assertIn('use_411',tasks);self.assertNotIn('use_49',tasks)

    def test_ride_stays_aboard_until_actual_destination_stop(self):
        s=self.scene(617,0);s['interactables'].pop(1)
        s['player']['p']=[-1478,-9551,632]
        self.assertEqual(mission(s)['transport'],'lift_riding')
        s['interactables'][1]['p'][2]=139;s['interactables'][1]['velocity'][2]=-10
        s['player']['p'][2]=154
        self.assertEqual(mission(s)['transport'],'lift_riding')
        s['interactables'][1]['velocity'][2]=0
        self.assertIsNone(mission(s)['transport'])

    def test_return_trip_boards_bottom_and_allows_real_48_second_ascent(self):
        s=self.scene(139,0);s['map']='c4m3_sugarmill_b';s['interactables'].pop(0)
        s['player']['p']=[-1400,-9400,154]
        self.assertEqual(mission(s)['transport'],'lift_boarding')
        s['interactables'].pop(0);s['interactables'][0]['p'][2]=400
        s['player']['p']=[-1478,-9551,415]
        self.assertEqual(mission(s)['transport'],'lift_riding')
        self.assertTrue(wait_for_elevator(s,48));self.assertFalse(wait_for_elevator(s,60))

    def test_transport_planner_restores_exit_after_ride(self):
        from mission_planner import MissionPlanner
        s=self.scene(617,0);mem=EvidenceMemory();mem.update(s);nav=FlatNav();nav.map=s['map']
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(nav,mem);planner.update(s)
            self.assertEqual(nav.goal,2)
            s['interactables'].pop(1);s['interactables'][1]['p'][2]=139
            s['player']['p']=[-1478,-9551,154];planner.update(s)
            self.assertEqual(nav.goal,planner.exit_goal)

    def test_wrapped_lift_button_uses_only_from_verified_unlocked_deck(self):
        s=self.scene(619,0);s['player']['p']=[-1488,-9576,621];s['player']['eye']=[-1488,-9576,683]
        button=s['interactables'][1];button['locked']=False;button['visible']=False
        from navigation import look
        pitch,yaw=look(s['player']['eye'],button['p']);s['player']['angles']=[pitch,yaw,0]
        def action():
            mem=EvidenceMemory();mem.update(s);motor=SkillMotor(FlatNav(),mem)
            return motor.tick(s,{'task':Task('use_411','use','Use inside button',button),'target_id':None,'combat':'hold'})
        self.assertIn('e',action()['keys'])
        button['locked']=True
        self.assertNotIn('e',action()['keys'])
        button['locked']=False;s['player']['p'][0]-=150
        self.assertNotIn('e',action()['keys'])

    def test_return_approach_is_retired_after_actual_inside_use(self):
        from mission_planner import MissionPlanner
        s=self.scene(139,0);s['map']='c4m3_sugarmill_b';s['interactables'].pop(0)
        s['player']['p']=[-1478,-9551,141];s['interactables'][0]['locked']=False
        mem=EvidenceMemory();mem.update(s);nav=FlatNav();nav.map=s['map']
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(nav,mem);planner.update(s);self.assertEqual(nav.goal,2)
            s['t']+=1;s['metrics']['last_use']={'target':411,'t':s['t']}
            s['interactables'].pop(0);s['interactables'][0]['p'][2]=619
            s['player']['p'][2]=621;planner.update(s)
            self.assertEqual(nav.goal,planner.exit_goal)
            resumed=MissionPlanner(nav,EvidenceMemory());resumed.exit_goal=3;resumed.update(s)
            self.assertEqual(nav.goal,3)


class FerryTransportTests(unittest.TestCase):
    def scene(self,x=-5440,boat=-4518,locked=True):
        s=state();s['map']='c3m1_plankcountry'
        s['player']['p']=[x,6064,28];s['player']['eye']=[x,6064,92]
        s['interactables']=[{'id':88,'name':'ferry_tram_button','type':'func_button','p':[boat,5964,38],'locked':locked,'disabled':False}]
        return s

    def test_arriving_boat_never_offers_walking_across_empty_water(self):
        s=self.scene();mem=EvidenceMemory();mem.update(s)
        _,_,tasks=packet(s,FlatNav(),mem)
        self.assertEqual(mission(s)['transport'],'arriving')
        self.assertEqual(set(tasks),{'hold'})
        s['interactables'].append({'id':87,'name':'ferry_button','type':'func_button','p':[-5444,5994,68],'locked':False,'disabled':False})
        self.assertIsNone(mission(s)['transport'])

    def test_arrival_boards_center_and_waits_for_team_before_use(self):
        s=self.scene(boat=-5248)
        self.assertEqual(mission(s)['transport'],'boarding')
        s['player']['p']=[-5248,6064,4];s['player']['eye']=[-5248,6064,68]
        mem=EvidenceMemory();mem.update(s)
        self.assertEqual(mission(s)['transport'],'gathering')
        self.assertEqual(set(packet(s,FlatNav(),mem)[2]),{'hold'})
        s['interactables'][0]['locked']=False
        self.assertEqual(mission(s)['transport'],'operate')
        self.assertIn('use_88',packet(s,FlatNav(),mem)[2])

    def test_departure_stays_on_actual_moving_deck_then_releases_at_far_bank(self):
        s=self.scene(x=-4900);s['interactables']=[{'id':130,'name':'ferry_door_right_entrance','type':'func_door','p':[-5013,6034,30]}]
        self.assertEqual(mission(s)['transport'],'riding')
        s['player']['p'][0]=-4496;s['interactables'][0]['p'][0]=-4609
        self.assertIsNone(mission(s)['transport'])
        s['player']['p'][0]=-5440;s['interactables'][0]['p'][0]=-5013
        self.assertIsNone(mission(s)['transport'])

    def test_ledge_help_wait_is_nearby_alive_and_bounded(self):
        from agent_controller import wait_for_ledge_rescue
        s=state();s['player']['ledge']=True;s['teammates']=[bot()]
        self.assertTrue(wait_for_ledge_rescue(s,3))
        self.assertTrue(wait_for_ledge_rescue(s,15))
        self.assertFalse(wait_for_ledge_rescue(s,45))
        s['teammates'][0]['dead']=True
        self.assertFalse(wait_for_ledge_rescue(s,3))
        s['teammates']=[bot(p=[900,0,0])]
        self.assertTrue(wait_for_ledge_rescue(s,3))
        s['teammates']=[bot(p=[1500,0,0])]
        self.assertFalse(wait_for_ledge_rescue(s,3))

    def test_completed_ferry_wait_does_not_become_instant_stale_travel_handoff(self):
        from agent_controller import reset_finished_wait
        s=self.scene(boat=-5248);mem=EvidenceMemory();mem.update(s)
        motor=SkillMotor(FlatNav(),mem);motor.task=Task('hold','hold','wait');motor.started=20
        mem.history.append((70,list(s['player']['p']),0,0,0))
        self.assertEqual(reset_finished_wait(s,mem,motor,'defend'),'travel')
        self.assertEqual(motor.started,s['t'])
        self.assertIsNone(progress_reason(s,mem,motor))

    def test_departed_ferry_does_not_keep_old_riding_instruction(self):
        from mission_planner import MissionPlanner
        s=self.scene(x=-4900);s['interactables']=[{'id':130,'name':'ferry_door_right_entrance','type':'func_door','p':[-5013,6034,30]}]
        mem=EvidenceMemory();mem.update(s);nav=FlatNav();nav.map=s['map']
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(nav,mem);planner.update(s)
            self.assertIn('Stay aboard',mem.planner_note)
            s['player']['p'][0]=-4496;s['interactables'][0]['p'][0]=-4609
            planner.update(s)
            self.assertNotIn('Stay aboard',mem.planner_note)
            self.assertEqual(nav.goal,planner.exit_goal)


class DecisionTimeoutTests(unittest.TestCase):
    def future(self,value=None,error=None):
        from concurrent.futures import Future
        f=Future()
        if error is not None:f.set_exception(error)
        else:f.set_result(value)
        return f

    def test_two_timeouts_are_bounded_and_success_resets_the_sequence(self):
        count=0
        for expected in (1,2):
            result,count=completed_request(self.future(error=TimeoutError()),count)
            self.assertIsNone(result);self.assertEqual(count,expected)
        response={'answers':{'task':'current-observation'}}
        self.assertEqual(completed_request(self.future(value=response),count),(response,0))
        with self.assertRaises(TimeoutError):completed_request(self.future(error=TimeoutError()),2)

    def test_budget_or_invalid_response_errors_are_never_auto_retried(self):
        with self.assertRaisesRegex(RuntimeError,'budget'):
            completed_request(self.future(error=RuntimeError('budget exhausted')),0)

    def test_gateway_recovery_is_bounded_and_preserves_uncertain_cost(self):
        from jev_bridge import JevClient,TransientJevError
        from unittest.mock import Mock
        for status,kind in ((502,TransientJevError),(503,TransientJevError),(504,TransientJevError),(401,RuntimeError),(402,RuntimeError),(429,RuntimeError)):
            with self.subTest(status=status),patch('jev_bridge.append_ledger') as ledger,patch('jev_bridge.CAP',20):
                client=JevClient.__new__(JevClient);client.key='unit-test';client.spent=0;client.connection=Mock()
                client.connection.getresponse.return_value.status=status
                client.connection.getresponse.return_value.read.return_value=b'{}'
                with self.assertRaises(kind) as caught:client.request({}, {})
                self.assertGreater(client.spent,0);self.assertEqual(ledger.call_count,1)
                client.connection.close.assert_called_once()
                if status in (502,503,504):
                    self.assertEqual(completed_request(self.future(error=caught.exception),1),(None,2))
                    with self.assertRaises(TransientJevError):completed_request(self.future(error=caught.exception),2)
                else:
                    self.assertNotIsInstance(caught.exception,TransientJevError)
                    with self.assertRaises(RuntimeError):completed_request(self.future(error=caught.exception),0)

    def test_zero_budget_refuses_before_network_or_ledger_mutation(self):
        from jev_bridge import JevClient
        from unittest.mock import Mock
        client=JevClient.__new__(JevClient)
        client.key='unit-test';client.spent=0;client.connection=Mock()
        with patch('jev_bridge.CAP',0),patch('jev_bridge.append_ledger') as ledger:
            with self.assertRaisesRegex(RuntimeError,'budget'):client.request({}, {})
        client.connection.request.assert_not_called()
        ledger.assert_not_called()
        self.assertEqual(client.spent,0)

def enemy(i=20,kind=0,p=None):return {'id':i,'type':kind,'health':50,'p':p or [120,0,64],'velocity':[0,0,0]}
def bot(i=2,p=None):return {'id':i,'p':p or [60,0,26],'health':60,'dead':False,'pinned':False,'incap':False,'ledge':False,'bot':True}

class FlatNav:
    goal=3;map='c1m2_streets'
    def __init__(self):self.areas={1:{'id':1,'p':[0,0,0]},2:{'id':2,'p':[200,0,0]},3:{'id':3,'p':[900,0,0]}}
    def waypoint(self,s,goal_area=None,**kwargs):return {'p':[200,0,0],'area':2,'attr':0,'remaining_areas':3,'link_type':'walk'}
    def closest_area(self,p,**kwargs):return 2
    def route(self,a,b,**kwargs):return [a,b]
    def update(self,s):pass
    def routes_to(self,a,goals,**kwargs):
        paths={}
        for b in goals:
            try:paths[b]=self.route(a,b,**kwargs)
            except RuntimeError:pass
        return paths

def choice(task='route',target=20,combat='fire'):
    return {'task':Task(task,task,'test'),'target_id':target,'combat':combat}

def scenarios():
    cases=[]
    s=state();s['enemies']=[enemy()]
    cases.append(('close_common_while_travelling',s,{'target':{'enemy_20'},'combat':{'fire','shove'}}))
    s=state();s['enemies']=[enemy(kind=5,p=[100,0,64])]
    cases.append(('jockey_about_to_pin',s,{'target':{'enemy_20'},'combat':{'shove'}}))
    s=state();s['enemies']=[enemy(kind=8,p=[100,0,64])]
    cases.append(('tank_cannot_be_shoved',s,{'target':{'enemy_20'},'combat':{'fire'}}))
    s=state();s['enemies']=[dict(enemy(kind=7),rage=0)]
    cases.append(('calm_witch_leave_alone',s,{'target':{'none'},'combat':{'hold'}}))
    s=state();s['player']['clip']=0;s['player']['weapons']['slot0']['clip']=0
    cases.append(('empty_gun_clear_area',s,{'combat':{'reload'},'task':{'route'}}))
    s=state();s['player']['health']=20
    cases.append(('heal_when_clear',s,{'task':{'heal'}}))
    s=state();s['player']['on_fire']=True;s['player']['health']=20
    cases.append(('escape_fire_before_medkit',s,{'task':{'escape'}}))
    s=state();s['player']['weapon']='weapon_adrenaline';s['player']['clip']=-1;s['player']['inventory']['slot4']='weapon_adrenaline';s['enemies']=[enemy()]
    cases.append(('medicine_equipped_attacked',s,{'target':{'enemy_20'},'combat':{'fire','shove'}}))
    s=state();s['teammates']=[dict(bot(),incap=True)]
    cases.append(('rescue_downed_teammate',s,{'task':{'rescue_2'}}))
    s=state();s['enemies']=[enemy(20,0,[90,80,64]),enemy(21,1,[400,0,64])];s['teammates']=[dict(bot(p=[400,20,0]),pinned=True)]
    cases.append(('smoker_holding_teammate',s,{'target':{'enemy_21'}}))
    s=state();s['player']['weapon']='weapon_cola_bottles';s['player']['clip']=-1
    s['interactables']=[{'id':41,'type':'point_prop_use_target','p':[70,0,64],'name':'','locked':False,'disabled':False}]
    cases.append(('deliver_held_cola',s,{'task':{'deliver_41'}}))
    s=state();s['map']='c1m3_mall';s['metrics']['hints']=[{'kind':'explain_mall_alarm','t':99}];s['interactables']=[{'id':51,'type':'func_button','p':[70,0,64],'name':'','hammerid':320879,'locked':False,'disabled':False}]
    cases.append(('alarm_switch_required',s,{'task':{'use_51'}}))
    return cases

class GeneralAgentTests(unittest.TestCase):
    def setUp(self):self.s=state();self.mem=EvidenceMemory();self.nav=FlatNav();self.motor=SkillMotor(self.nav,self.mem)
    def test_closed_door_on_observed_walking_ray_preempts_route(self):
        door={'id':61,'type':'prop_door_rotating_checkpoint','p':[70,0,54],'visible':True,'door_state':0}
        self.s['interactables']=[door];self.s['obstacles']=[{'angle':0,'fraction':.4,'hit_id':61}]
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertIn('use_61',tasks);self.assertNotIn('route',tasks)
        self.s['obstacles'][0]['angle']=90
        self.assertIn('route',packet(self.s,self.nav,self.mem)[2])
        self.s['obstacles'][0].update(angle=0,hit_id=99)
        self.assertIn('route',packet(self.s,self.nav,self.mem)[2])

    def test_remote_interaction_does_not_steer_ladder_climb(self):
        self.s['player'].update(p=[16,0,200],eye=[16,0,262],angles=[65,180,0],on_ladder=True)
        door={'id':201,'type':'prop_door_rotating_checkpoint','p':[400,300,54],'visible':False,'door_state':2}
        self.s['interactables']=[door]
        step={'p':[-20,0,0],'area':2,'attr':0,'link_type':'ladder','phase':'climb',
            'pitch':65,'face_yaw':180,'ladder_center':[0,0,384]}
        decision={'task':Task('use_201','use','Close observed entrance',door),'target_id':None,'combat':'hold'}
        with patch.object(self.nav,'waypoint',return_value=step):
            action=self.motor.tick(self.s,decision)
        self.assertEqual(action['dx'],0);self.assertEqual(action['dy'],0)
        self.assertEqual(action['keys'],['w']);self.assertNotIn('e',action['keys'])

    def test_close_ladder_landing_releases_attachment_without_large_drop(self):
        self.s['player'].update(p=[9291.6,3262.5,213.9],eye=[9291.6,3262.5,275.9],angles=[5,180,0],on_ladder=True)
        step={'p':[9287.5,3262.5,205.95],'area':2,'attr':0,'link_type':'ladder','phase':'dismount','pitch':5}
        with patch.object(self.nav,'waypoint',return_value=step):
            action=self.motor.tick(self.s,choice(combat='hold',target=None))
            self.assertIn('space',action['keys']);self.assertIn('w',action['keys'])
            self.s['t']+=.1
            self.assertNotIn('space',self.motor.tick(self.s,choice(combat='hold',target=None))['keys'])
            self.s['t']+=1;self.s['player']['p'][2]+=100
            self.assertNotIn('space',self.motor.tick(self.s,choice(combat='hold',target=None))['keys'])

    def test_multifloor_checkpoint_approach_precedes_short_door_use(self):
        from mission_planner import MissionPlanner
        self.s.update(map='c5m4_quarter',round_id='quarter-1',exit_checkpoint_occupied=True)
        self.s['player'].update(p=[0,0,384],eye=[0,0,446])
        self.nav.map=self.s['map'];self.nav.areas[1].update(spawn=2048,p=[0,0,384])
        self.nav.areas[3]['p']=[50,0,384]
        door={'id':201,'type':'prop_door_rotating_checkpoint','p':[80,0,54],'visible':False,'door_state':2}
        self.s['interactables']=[door]
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(self.nav.goal,2);self.assertEqual(planner.intent['id'],201)
            self.assertNotIn('use_201',packet(self.s,self.nav,self.mem)[2])
            self.s['player'].update(p=[20,0,0],eye=[20,0,62])
            self.assertIn('use_201',packet(self.s,self.nav,self.mem)[2])
            door['door_state']=0;planner.update(self.s)
            self.assertIsNone(planner.intent);self.assertEqual(self.nav.goal,3)

    def test_narrow_ladder_centers_before_repeating_forward_climb(self):
        self.s['player'].update(p=[16.6,-11.2,41],eye=[16.6,-11.2,103],angles=[-65,180,0],on_ladder=True)
        step={'p':[-20,0,128],'area':2,'attr':0,'link_type':'ladder','phase':'climb',
            'pitch':-65,'face_yaw':180,'ladder_center':[0,0,8]}
        with patch.object(self.nav,'waypoint',return_value=step):
            action=self.motor.tick(self.s,choice(combat='hold',target=None))
            self.assertIn('d',action['keys']);self.assertIn('w',action['keys']);self.assertEqual(action['dx'],0)
            self.s['player'].update(p=[16.6,0,80],eye=[16.6,0,142]);self.s['t']+=.1
            action=self.motor.tick(self.s,choice(combat='hold',target=None))
            self.assertEqual(action['keys'],['w'])
    def test_bridge_precise_railing_keeps_walk_speed_during_running_finale(self):
        self.s['map']='c5m5_bridge';self.s['events']=[{'kind':'gauntlet_finale_start','t':0}];self.s['t']=100
        self.s['enemies']=[]
        step={'p':[100,0,0],'area':2,'attr':536870916,'link_type':'walk','align_portal':False}
        with patch.object(self.nav,'waypoint',return_value=step):
            action=self.motor.tick(self.s,choice(combat='hold',target=None))
        self.assertIn('shift',action['keys'])
        self.assertTrue(set(action['keys'])&{'w','a','s','d'})
    def test_ladder_approach_corrects_small_lateral_offset_at_walking_speed(self):
        self.s['player'].update(p=[52,-11.2,0],eye=[52,-11.2,62],angles=[0,180,0])
        step={'p':[32,0,8],'area':2,'attr':0,'link_type':'ladder','phase':'approach',
            'pitch':0,'face_yaw':180,'ladder_center':[0,0,8]}
        with patch.object(self.nav,'waypoint',return_value=step):
            action=self.motor.tick(self.s,choice(combat='hold',target=None))
            self.assertIn('shift',action['keys']);self.assertNotIn('space',action['keys'])
            yaw=math.radians(180-action['dx']*.066);keys=action['keys']
            toward_center=(int('w' in keys)-int('s' in keys))*math.sin(yaw)+(int('a' in keys)-int('d' in keys))*math.cos(yaw)
            self.assertGreater(toward_center,0)
    def test_running_finale_still_walks_short_portal_correction(self):
        self.s['map']='c5m5_bridge';self.s['events']=[{'kind':'gauntlet_finale_start','t':0}];self.s['t']=100
        self.s['enemies']=[]
        step={'p':[20,0,0],'area':2,'attr':0,'link_type':'walk','align_portal':True}
        with patch.object(self.nav,'waypoint',return_value=step):
            action=self.motor.tick(self.s,choice(combat='hold',target=None))
            self.assertIn('shift',action['keys'])
            step['p']=[110,0,0];self.s['t']+=.1
            action=self.motor.tick(self.s,choice(combat='hold',target=None))
            self.assertNotIn('shift',action['keys'])
    def test_combat_aim_retains_a_visible_target_briefly_then_allows_switch(self):
        focus=CombatFocus();self.s['enemies']=[enemy(),enemy(21,p=[-120,0,64])]
        self.assertEqual(focus.select(self.s,choice(target=20))[0]['id'],20)
        self.s['t']+=.3
        self.assertEqual(focus.select(self.s,choice(target=21))[0]['id'],20)
        self.s['t']+=.7
        self.assertEqual(focus.select(self.s,choice(target=21))[0]['id'],21)
    def test_combat_focus_drops_disappeared_targets_immediately(self):
        focus=CombatFocus();self.s['enemies']=[enemy()]
        focus.select(self.s,choice());self.s['t']+=.1
        self.s['enemies']=[enemy(21,p=[80,0,64])]
        selected,combat=focus.select(self.s,None)
        self.assertEqual(selected['id'],21);self.assertEqual(combat,'fire')
        self.s['enemies']=[]
        self.assertIsNone(focus.select(self.s,None)[0])
    def test_incoming_hunter_preempts_common_choice_before_pinning(self):
        focus=CombatFocus();self.s['enemies']=[enemy(p=[144,0,64]),enemy(21,kind=3,p=[169,20,64])]
        picked,combat=focus.select(self.s,choice(target=20))
        self.assertEqual(picked['id'],21);self.assertEqual(combat,'fire')
        self.s['enemies'][1]['p']=[100,20,64];self.s['t']+=1
        picked,combat=focus.select(self.s,choice(target=20))
        self.assertEqual(picked['id'],21);self.assertEqual(combat,'shove')
        self.s['enemies'][1]['p']=[900,20,64];self.s['t']+=1
        self.assertEqual(focus.select(self.s,choice(target=20))[0]['id'],20)
    def test_immediate_defense_respects_explicit_hold_and_calm_witch(self):
        focus=CombatFocus();self.s['enemies']=[enemy()]
        self.assertIsNone(focus.select(self.s,choice(target=None,combat='hold'))[0])
        self.assertEqual(focus.select(self.s,None)[0]['id'],20)
        self.s['enemies']=[dict(enemy(),type=7,rage=0)]
        self.assertIsNone(focus.select(self.s,None)[0])
    def test_pinning_special_and_tank_can_preempt_combat_focus(self):
        for special in (False,True):
            with self.subTest(special=special):
                focus=CombatFocus();self.s['enemies']=[enemy()];self.s['teammates']=[]
                focus.select(self.s,choice());self.s['t']+=.1
                urgent=enemy(21,kind=5 if special else 8,p=[220,220,64])
                self.s['enemies'].append(urgent)
                if special:self.s['teammates']=[dict(bot(p=[230,230,0]),pinned=True)]
                self.assertEqual(focus.select(self.s,choice(target=21))[0]['id'],21)
    def test_combat_focus_does_not_hold_a_friendly_blocked_target(self):
        focus=CombatFocus();self.s['enemies']=[enemy(),enemy(21,p=[0,120,64])]
        focus.select(self.s,choice());self.s['t']+=.1
        self.s['teammates']=[bot(p=[60,0,26])]
        self.assertEqual(focus.select(self.s,choice(target=21))[0]['id'],21)
    def test_incapacitated_defense_fires_instead_of_attempting_a_shove(self):
        self.s['player'].update(incap=True,immobilized=True,weapon='weapon_pistol',clip=5)
        self.s['player']['weapons']['slot1']={'type':'weapon_pistol','clip':5,'reserve':0,'max_clip':15,'next_attack':0,'reloading':False}
        self.s['enemies']=[enemy()]
        self.assertNotIn('shove',packet(self.s,self.nav,self.mem)[1]['combat']['criteria'])
        action=self.motor.tick(self.s,choice(task='hold',combat='shove'))
        self.assertIn('fire',action['buttons']);self.assertNotIn('shove',action['buttons'])
    def test_occluded_tank_does_not_reverse_retreat_or_become_a_stale_firing_target(self):
        self.s['enemies']=[enemy(kind=8,p=[-250,0,64])]
        step={'p':[200,0,0],'area':2,'attr':0,'link_type':'walk'}
        with patch.object(self.motor,'safe_escape',return_value=step):
            self.assertEqual(self.motor.tick(self.s,choice('route',None,'hold'))['task'],'evade')
            self.s['t']+=.5;self.s['enemies']=[]
            action=self.motor.tick(self.s,choice('route',None,'hold'))
            self.assertEqual(action['task'],'evade');self.assertTrue(action['keys'])
            self.assertIsNone(action['target_id']);self.assertNotIn('fire',action['buttons'])
            self.s['t']+=3.1
            self.assertEqual(self.motor.tick(self.s,choice('route',None,'hold'))['task'],'route')
    def test_visible_distant_tank_clears_short_occlusion_retreat(self):
        self.s['enemies']=[enemy(kind=8,p=[-250,0,64])]
        step={'p':[200,0,0],'area':2,'attr':0,'link_type':'walk'}
        with patch.object(self.motor,'safe_escape',return_value=step):
            self.motor.tick(self.s,choice('route',None,'hold'))
            self.s['t']+=.5;self.s['enemies'][0]['p']=[-950,0,64]
            self.assertEqual(self.motor.tick(self.s,choice('route',None,'hold'))['task'],'route')
    def test_empty_staging_floor_falls_back_to_actual_upper_fuel(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['metrics']['flags']={'finale_start':True}
            cfg={'enabled':True,'map':self.s['map'],'round_id':'current','observer':'Astra visual review in current thread','reviewed_image':'reviewed.png','required_cans':8,'spots':[{'id':'balcony','p':[0,0,280]}]}
            root.joinpath('fuel-staging-config.json').write_text(json.dumps(cfg))
            root.joinpath('fuel-search-sites.json').write_text(json.dumps({'c1m4_atrium':[{'id':'unconfirmed','p':[900,0,280]}]}))
            planner=FuelPlanner(root,self.nav);planner.update(self.s,self.mem)
            planner.data['staging_complete']=True
            self.s['items']=[{'id':42,'type':'weapon_gascan','p':[200,0,280]}]
            self.assertEqual(planner.update(self.s,self.mem)['id'],42)
    def test_tank_retreat_does_not_oscillate_with_nearby_fuel_pickup(self):
        can={'id':165,'type':'weapon_gascan','p':[30,0,4]}
        self.s['items']=[can]
        d=choice(target=None,combat='hold');d['task']=Task('carry_165','carry','Collect fuel',can)
        waypoint={'p':[200,0,0],'area':2,'attr':0,'link_type':'walk'}
        with patch.object(self.motor,'safe_escape',return_value=waypoint):
            self.s['enemies']=[enemy(kind=8,p=[500,0,64])]
            self.assertEqual(self.motor.tick(self.s,d)['task'],'evade')
            self.s['enemies'][0]['p']=[650,0,64];self.s['t']+=1
            self.assertEqual(self.motor.tick(self.s,d)['task'],'evade')
            self.s['enemies'][0]['p']=[900,0,64];self.s['t']+=1
            self.assertEqual(self.motor.tick(self.s,d)['task'],'carry')
    def test_tank_retreat_ends_after_bounded_absence_of_live_tank(self):
        waypoint={'p':[200,0,0],'area':2,'attr':0,'link_type':'walk'}
        with patch.object(self.motor,'safe_escape',return_value=waypoint):
            self.s['enemies']=[enemy(kind=8,p=[500,0,64])]
            self.assertEqual(self.motor.tick(self.s,choice(target=None,combat='hold'))['task'],'evade')
            self.s['enemies']=[];self.s['t']+=3.1
            self.assertEqual(self.motor.tick(self.s,choice(target=None,combat='hold'))['task'],'route')
    def test_confirmed_inactive_tank_ends_retreat_without_occlusion_delay(self):
        waypoint={'p':[200,0,0],'area':2,'attr':0,'link_type':'walk'}
        with patch.object(self.motor,'safe_escape',return_value=waypoint):
            self.s['enemies']=[enemy(kind=8,p=[500,0,64])]
            self.motor.tick(self.s,choice(target=None,combat='hold'))
            self.s['inactive_enemies']=[dict(self.s['enemies'][0],incap=True)]
            self.s['enemies']=[];self.s['t']+=.1
            self.assertEqual(self.motor.tick(self.s,choice(target=None,combat='hold'))['task'],'route')
    def test_nearby_occluded_pickup_uses_doorway_without_blind_interaction(self):
        item={'id':165,'type':'weapon_gascan','p':[60,0,4]}
        d=choice(target=None,combat='hold');d['task']=Task('carry_165','carry','Recover fuel behind a corner',item)
        action=self.motor.tick(self.s,d)
        self.assertEqual(action['waypoint']['area'],2)
        self.assertTrue(action['keys']);self.assertNotIn('e',action['keys'])
        self.assertFalse(action['buttons'])
        self.s['t']+=12.1
        action=self.motor.tick(self.s,d)
        self.assertEqual(action['reason'],'target absent')
        self.assertFalse(action['keys']);self.assertFalse(action['buttons'])
    def test_fuel_across_nearby_wall_is_not_discarded_before_reaching_its_floor(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            planner=FuelPlanner(Path(directory),self.nav)
            self.s['metrics']['flags']={'finale_start':True}
            can={'id':165,'type':'weapon_gascan','p':[60,0,4],'owned':False}
            self.s['items']=[can];planner.update(self.s,self.mem)
            self.s['items']=[];self.s['t']+=4
            planner.update(self.s,self.mem)
            self.assertIn('165',planner.data['cans'])
            self.s['player']['area']=2
            planner.update(self.s,self.mem)
            self.assertNotIn('165',planner.data['cans'])
    def test_critical_player_can_wait_for_observed_bot_medkit(self):
        self.s['player']['health']=1;self.s['player']['inventory'].pop('slot3')
        self.s['teammates']=[dict(bot(),has_medkit=True)]
        data,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertEqual(set(tasks),{'await_aid'});self.assertTrue(data['team'][0]['has_medkit'])
        d=choice(target=None,combat='hold');d['task']=tasks['await_aid']
        self.assertFalse(self.motor.tick(self.s,d)['keys'])
        self.s['t']+=12
        self.assertIn('bounded aid wait',progress_reason(self.s,self.mem,self.motor))
    def test_aid_wait_needs_an_available_nearby_equipped_healthy_bot(self):
        self.s['player']['health']=1;self.s['player']['inventory'].pop('slot3')
        for change in ({'has_medkit':False},{'health':20},{'p':[500,0,0]},{'incap':True},{'pinned':True},{'bot':False}):
            self.s['teammates']=[dict(bot(),has_medkit=True,**change)] if 'has_medkit' not in change else [dict(bot(),**change)]
            self.assertNotIn('await_aid',packet(self.s,self.nav,self.mem)[2])
    def test_attacker_or_own_medkit_takes_precedence_over_waiting_for_aid(self):
        self.s['player']['health']=1;self.s['teammates']=[dict(bot(),has_medkit=True)]
        self.assertEqual(set(packet(self.s,self.nav,self.mem)[2]),{'heal'})
        self.s['player']['inventory'].pop('slot3');self.s['enemies']=[enemy()]
        self.assertNotIn('await_aid',packet(self.s,self.nav,self.mem)[2])
    def test_healed_player_does_not_keep_waiting_for_bot_aid(self):
        self.s['player']['health']=80;self.s['player']['inventory'].pop('slot3')
        self.s['teammates']=[dict(bot(),has_medkit=True)]
        self.assertNotIn('await_aid',packet(self.s,self.nav,self.mem)[2])
    def test_low_health_uses_clear_healing_interval_before_more_travel(self):
        from agent_policy import tasks_for
        self.s['player']['health']=58
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertEqual(set(tasks),{'heal'})
        self.assertTrue(tasks['heal'].critical)
        self.s['teammates']=[dict(bot(),incap=True)]
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertEqual(set(tasks),{'heal','rescue_2'})
        self.s['teammates']=[];self.s['enemies']=[enemy(p=[100,0,64])]
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertNotIn('heal',tasks);self.assertIn('route',tasks)
        self.s['enemies']=[];self.mem.cooldowns['heal']=self.s['t']+2
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertNotIn('heal',tasks);self.assertIn('route',tasks)
    def test_falling_can_prefers_reachable_floor_over_isolated_stair_polygon(self):
        nav=Navigation('c1m4_atrium');point=[-3933.7712,-4271.2207,153.4272]
        self.assertEqual(nav.closest_area(point,below=True),2645)
        with self.assertRaises(NoRouteError):nav.route(10162,2645)
        floor=nav.closest_area(point,below=True,reachable_from=10162)
        self.assertEqual(floor,98);self.assertEqual(nav.route(10162,floor),[10162,98])
    def test_dropped_fuel_does_not_route_to_the_ledge_above_it(self):
        nav=Navigation('c1m4_atrium')
        dropped=[-5399.5581,-4002.511,339.7204]
        self.assertEqual(nav.closest_area(dropped),38795)
        with self.assertRaises(NoRouteError):nav.route(413,38795)
        floor=nav.closest_area(dropped,below=True)
        self.assertEqual(floor,413)
        self.assertEqual(nav.route(413,floor),[413])
        # The selection also stays on the same real floor after the can lands.
        dropped[2]=284
        self.assertEqual(nav.closest_area(dropped,below=True),413)
    def test_pickup_planning_uses_a_floor_below_the_object(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['map']='c1m4_atrium';self.nav.map=self.s['map']
            self.s['round_id']='dropped-can';self.s['metrics']['flags']={'finale_start':True}
            self.s['items']=[{'id':55,'type':'weapon_gascan','p':[200,0,60]}]
            planner=MissionPlanner(self.nav,self.mem)
            with patch.object(self.nav,'closest_area',wraps=self.nav.closest_area) as area:
                planner.update(self.s)
                self.assertEqual(planner.intent['id'],55)
                self.assertTrue(all(call.kwargs.get('below') for call in area.call_args_list))
            d=choice(target=None,combat='hold');d['task']=Task('carry_55','carry','Recover dropped fuel',self.s['items'][0])
            with patch.object(self.nav,'closest_area',wraps=self.nav.closest_area) as area:
                self.motor.tick(self.s,d)
                area.assert_called_with([200,0,60],below=True,reachable_from=1)
    def test_current_planner_can_request_a_review_position_while_carrying(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['round_id']='current';self.s['player']['weapon']='weapon_cola_bottles'
            self.mem.remembered_items={55:{'item':{'id':55,'type':'point_prop_use_target','p':[200,0,64]}}}
            directive={'map':self.s['map'],'round_id':'current','version':'review','created_game_t':99,
                'expires_game_t':130,'goal_area':3,'public_instruction':'Carry the item to this position for visual review.'}
            path=Path(directory)/'planner-directive.json';path.write_text(json.dumps(directive))
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(self.nav.goal,2)
            directive['override_mission_goal']=True;path.write_text(json.dumps(directive));planner.update(self.s)
            self.assertEqual(self.nav.goal,3)
            self.assertIsNone(self.mem.navigation_goal)
            directive['round_id']='old';path.write_text(json.dumps(directive));planner.update(self.s)
            self.assertEqual(self.nav.goal,2)
    def test_partial_async_plan_keeps_delivery_and_accepts_next_valid_update(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['round_id']='current';self.s['player']['weapon']='weapon_cola_bottles'
            self.mem.remembered_items={55:{'item':{'id':55,'type':'point_prop_use_target','p':[200,0,64]}}}
            directive={'map':self.s['map'],'round_id':'current','version':'review','created_game_t':99,
                'expires_game_t':130,'goal_area':3,'override_mission_goal':True,
                'public_instruction':'Carry the item to the review position.'}
            path=Path(directory)/'planner-directive.json';path.write_text(json.dumps(directive))
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(self.nav.goal,3)
            for partial in ('','{"version":'):
                path.write_text(partial);planner.update(self.s)
                self.assertEqual(self.nav.goal,2)
                self.assertEqual(planner.intent['id'],55)
            path.write_text(json.dumps(directive));planner.update(self.s)
            self.assertEqual(self.nav.goal,3)
    def test_successful_pickup_resets_prior_failures(self):
        self.mem.outcome('carry_55','failed',100,'Occluded')
        self.mem.outcome('carry_55','verified',110,'Held entity confirmed')
        self.mem.outcome('carry_55','failed',130,'Target disappeared')
        self.assertEqual(self.mem.failures['carry_55'],1)
    def test_pickup_below_an_unreached_elevator_is_not_offered(self):
        self.s['map']='c1m4_atrium'
        self.s['items']=[{'id':55,'type':'weapon_gascan','p':[900,0,-280]}]
        with patch.object(self.nav,'closest_area',side_effect=NoRouteError('floor inaccessible')):
            tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertNotIn('carry_55',tasks);self.assertIn('route',tasks)
        self.assertNotIn('carry_55',packet(self.s,self.nav,self.mem)[2])
        self.s['player'].update(p=[750,0,-280],eye=[750,0,-216])
        self.assertIn('carry_55',packet(self.s,self.nav,self.mem)[2])
    def test_open_checkpoint_door_uses_the_narrow_opening_center(self):
        n=Navigation('c1m4_atrium');n.goal=5822
        s=state();s['map']='c1m4_atrium'
        s['player'].update(area=818,p=[-2008.8572,-4759.0557,536.0313])
        step=n.waypoint(s)
        self.assertEqual(step['area'],3025)
        self.assertAlmostEqual(step['p'][0],-2012.5)
        self.assertLess(step['p'][1],-4800)

    def test_portal_alignment_first_clears_the_opposite_wall(self):
        nav=Navigation('c1m4_atrium');nav.avoided=set()
        s=copy.deepcopy(self.s);s['map']='c1m4_atrium'
        s['player'].update(area=19993,p=[-4048.855,-2912.0313,280.0313],nearest_area=True)
        first=nav.waypoint(s,3294)
        self.assertTrue(first['align_portal'])
        self.assertLess(first['p'][0],s['player']['p'][0]-20)
        self.assertEqual(first['p'][1],s['player']['p'][1])
        s['player']['p'][0]-=8;s['player']['nearest_area']=False
        midway=nav.waypoint(s,3294)
        self.assertEqual(midway['p'],first['p'])
        s['player']['p']=first['p']
        second=nav.waypoint(s,3294)
        self.assertEqual(second['p'][0],first['p'][0])
        self.assertGreater(second['p'][1],first['p'][1]+30)
    def test_pickup_that_becomes_unreachable_releases_without_interaction(self):
        item={'id':55,'type':'weapon_gascan','p':[900,0,-280]};self.s['items']=[item]
        d=choice(target=None,combat='hold');d['task']=Task('carry_55','carry','Collect fuel',item)
        with patch.object(self.nav,'closest_area',side_effect=NoRouteError('floor inaccessible')):
            action=self.motor.tick(self.s,d)
        self.assertEqual(action['keys'],[]);self.assertEqual(action['buttons'],[])
        self.assertIsNone(self.motor.task);self.assertFalse(self.mem.allowed('carry_55',self.s['t']))
    def test_consumed_fuel_is_not_reselected_from_a_lingering_observation(self):
        self.s['map']='c1m4_atrium';self.mem.consumed_fuel={55}
        item={'id':55,'type':'weapon_gascan','p':[70,0,64]};self.s['items']=[item]
        self.assertNotIn('carry_55',packet(self.s,self.nav,self.mem)[2])
        d=choice(target=None,combat='hold');d['task']=Task('carry_55','carry','Old pickup decision',item)
        self.assertEqual(self.motor.tick(self.s,d)['task'],'route')
    def test_teammate_healing_does_not_interrupt_our_unfinished_medkit(self):
        self.s['player'].update(health=20,weapon='weapon_first_aid_kit')
        self.motor.tick(self.s,choice('heal',None,'hold'))
        later=copy.deepcopy(self.s);later['t']+=3;later['metrics']['heals']=1
        action=self.motor.tick(later,None)
        self.assertIn('fire',action['buttons'])
        self.assertFalse(self.mem.outcomes)
    def test_visible_ammo_beyond_use_range_follows_floor_route_around_counter(self):
        item={'id':55,'type':'weapon_ammo_spawn','p':[0,214,46]}
        self.s['items']=[item]
        d=choice(target=None,combat='hold');d['task']=Task('loot_55','loot','Refill ammo',item)
        a=self.motor.tick(self.s,d)
        self.assertEqual(a['waypoint']['p'],[200,0,0])
        self.assertNotIn('e',a['keys'])
    def test_actual_elevator_movement_clears_old_stall_but_stationary_wait_is_bounded(self):
        self.motor.task=Task('route','route','Ride elevator')
        for i in range(30):
            self.s['t']=100+i;self.s['player']['p']=[0,0,536];self.mem.update(copy.deepcopy(self.s))
        self.s['t']=130;self.s['player']['p']=[0,0,521]
        self.s['interactables']=[{'id':55,'type':'func_elevator','p':[0,0,517]}]
        self.mem.update(copy.deepcopy(self.s))
        self.assertIsNone(progress_reason(self.s,self.mem,self.motor))
        for i in range(28):
            self.s['t']=131+i;self.mem.update(copy.deepcopy(self.s))
        self.assertIsNotNone(progress_reason(self.s,self.mem,self.motor))
    def test_occluded_elevator_button_is_approached_but_not_used_blindly(self):
        self.s['map']='c1m4_atrium'
        item={'id':55,'type':'func_button','name':'button_elev_3rdfloor','p':[70,0,64],'visible':False}
        self.s['interactables']=[item]
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertIn('use_55',tasks)
        d=choice(target=None,combat='hold');d['task']=tasks['use_55']
        a=self.motor.tick(self.s,d)
        self.assertIn('w',a['keys']);self.assertNotIn('e',a['keys'])
        self.s['interactables'][0]['visible']=True;self.s['t']+=.4
        self.assertIn('e',self.motor.tick(self.s,d)['keys'])
    def test_nearby_button_press_is_not_prevented_by_aiming_at_a_distant_common(self):
        item={'id':55,'type':'func_button','name':'button_elev_3rdfloor','p':[70,0,64],'visible':True}
        self.s['map']='c1m4_atrium';self.s['interactables']=[item]
        self.s['enemies']=[enemy(p=[0,800,64])]
        d=choice();d['task']=Task('use_55','use','Start elevator',item)
        a=self.motor.tick(self.s,d)
        self.assertIn('e',a['keys']);self.assertNotIn('fire',a['buttons'])
        self.assertEqual(a['dx'],0)
    def test_clustered_pickup_requires_aim_on_requested_item(self):
        item={'id':55,'type':'weapon_spawn','p':[70,10,64]}
        self.s['items']=[item]
        d=choice(target=None,combat='hold');d['task']=Task('loot_55','loot','Pick up this gun',item)
        self.assertNotIn('e',self.motor.tick(self.s,d)['keys'])
        self.s['player']['angles']=[0,8.13,0]
        self.s['t']+=.4
        self.assertIn('e',self.motor.tick(self.s,d)['keys'])
    def test_wounded_player_defends_instead_of_repeating_fuel_pickup(self):
        self.s['map']='c1m4_atrium';self.s['player']['health']=30
        self.s['items']=[{'id':40,'type':'weapon_gascan','p':[50,0,10]}]
        self.s['enemies']=[enemy(p=[80,0,64])]
        self.assertNotIn('carry_40',packet(self.s,self.nav,self.mem)[2])
        d=choice();d['task']=Task('carry_40','carry','Collect fuel',self.s['items'][0])
        self.assertEqual(self.motor.tick(self.s,d)['task'],'hold')
        self.s['enemies']=[];self.s['t']+=6
        self.assertEqual(set(packet(self.s,self.nav,self.mem)[2]),{'heal'})
        self.s['player']['health']=80;self.s['player']['inventory'].pop('slot3')
        self.assertIn('carry_40',packet(self.s,self.nav,self.mem)[2])
    def test_fall_damage_does_not_drop_fuel_for_a_distant_common(self):
        self.s['player'].update(weapon='weapon_gascan',clip=-1)
        self.mem.recent_damage=34;self.s['enemies']=[enemy(p=[0,600,64])]
        action=self.motor.tick(self.s,choice())
        self.assertNotIn('1',action['keys']);self.assertNotIn('2',action['keys'])
        self.s['player']['health']=25;self.s['enemies'][0]['p']=[100,0,64]
        self.assertIn('1',self.motor.tick(self.s,choice())['keys'])
    def test_returning_with_two_rifle_magazines_prioritizes_ammo(self):
        self.s['player']['weapons']['slot0'].update(type='weapon_rifle_ak47',clip=40,max_clip=40,reserve=81)
        self.s['items']=[{'id':55,'type':'weapon_ammo_spawn','p':[500,0,0]}]
        self.assertEqual(set(packet(self.s,self.nav,self.mem)[2]),{'loot_55'})
    def test_observed_carousel_shutoff_precedes_long_ammo_backtrack(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['map']=self.nav.map='c2m2_fairgrounds'
            button={'id':486,'type':'func_button','name':'carousel_button','p':[800,0,64],'visible':False}
            ammo={'id':593,'type':'weapon_ammo_spawn','p':[-1600,0,64]}
            self.s['player']['weapons']['slot0'].update(type='weapon_rifle',max_clip=50,clip=5,reserve=12)
            self.mem.remembered_items={486:{'item':button},593:{'item':ammo}}
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(planner.intent['id'],486)
            self.assertTrue(self.mem.navigation_goal['urgent_event'])
            self.assertIn('stop carousel',self.mem.navigation_goal['purpose'])
            self.s['metrics']['last_use']={'target':486,'t':self.s['t']+.1};self.s['t']+=.2
            planner.update(self.s);self.assertEqual(planner.intent['id'],593)
            # Re-reading the still-observed entity must not erase actual Use.
            planner.update(self.s);self.assertEqual(planner.intent['id'],593)
    def test_event_control_approach_expires_and_never_drops_carried_object(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['map']=self.nav.map='c2m2_fairgrounds'
            button={'id':486,'type':'func_button','name':'carousel_button','p':[800,0,64]}
            self.mem.remembered_items={486:{'item':button}}
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(planner.intent['id'],486)
            self.s['player']['weapon']='weapon_gascan';planner.update(self.s)
            self.assertIsNone(planner.intent)
            self.s['player']['weapon']='weapon_hunting_rifle';self.s['t']+=601
            self.mem.remembered_items={};planner.update(self.s)
            self.assertIsNone(planner.intent)
    def test_remembered_tractor_start_precedes_still_blocked_exit(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['map']=self.nav.map='c5m4_quarter'
            button={'id':585,'type':'func_button','name':'tractor_button','p':[800,0,64],'visible':False}
            self.s['interactables']=[button];self.mem.remembered_items={585:{'item':button}}
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(planner.intent['id'],585);self.assertEqual(self.nav.goal,2)
            self.assertFalse(self.mem.navigation_goal['urgent_event'])
            self.assertIn('tractor',self.mem.navigation_goal['purpose'])
            self.s['metrics']['last_use']={'target':585,'t':100.1};self.s['t']=100.2
            planner.update(self.s);self.assertIsNone(planner.intent)
            self.assertEqual(planner.phase,'defend')
            self.s['t']=166.2;planner.update(self.s)
            self.assertEqual(planner.phase,'travel');self.assertEqual(self.nav.goal,3)
    def test_event_start_does_not_override_critical_ammo_preparation(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['map']=self.nav.map='c5m4_quarter'
            button={'id':585,'type':'func_button','name':'tractor_button','p':[800,0,64],'visible':False}
            ammo={'id':50,'type':'weapon_ammo_spawn','p':[400,0,64]}
            self.s['interactables']=[button]
            self.s['player']['weapons']['slot0'].update(type='weapon_rifle',max_clip=50,clip=0,reserve=0)
            self.mem.remembered_items={585:{'item':button},50:{'item':ammo}}
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(planner.intent['id'],50)
    def test_consumed_carousel_control_does_not_keep_approach_goal(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['map']=self.nav.map='c2m2_fairgrounds'
            button={'id':486,'type':'func_button','name':'carousel_button','p':[60,0,64]}
            self.s['interactables']=[button];self.mem.remembered_items={486:{'item':button}}
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.s['interactables']=[];self.s['t']+=.1;planner.update(self.s)
            self.assertIsNotNone(planner.intent)
            self.s['t']+=.4;planner.update(self.s);self.assertIsNone(planner.intent)
            planner.update(self.s);self.assertIsNone(planner.intent)
            self.assertEqual(self.s['outcome'],'active')
    def test_missing_or_occluded_control_is_not_automatically_consumed(self):
        from game_knowledge import consumed_control
        self.s['map']='c2m2_fairgrounds'
        button={'id':486,'type':'func_button','name':'carousel_button','p':[60,0,64]}
        self.s['interactables']=[dict(button,visible=False)]
        self.assertFalse(consumed_control(self.s,button))
        self.s['interactables']=[]
        self.assertFalse(consumed_control(self.s,dict(button,p=[800,0,64])))
        self.assertFalse(consumed_control(self.s,dict(button,name='unknown_button')))
        self.s['map']='c2m3_coaster';self.assertFalse(consumed_control(self.s,button))
    def test_single_use_button_disappearance_acknowledges_issued_use_only(self):
        self.s['map']='c2m2_fairgrounds'
        button={'id':486,'type':'func_button','name':'carousel_button','p':[60,0,64]}
        self.motor._start(Task('use_486','use','stop carousel',button),self.s)
        self.s['t']+=.5;self.motor._verify(self.s);self.assertIsNotNone(self.motor.task)
        self.motor.last_use=self.motor.started;self.motor._verify(self.s)
        self.assertIsNone(self.motor.task);self.assertEqual(self.mem.outcomes[-1]['status'],'input_accepted')
    def test_hunting_rifle_can_upgrade_to_observed_automatic_rifle(self):
        self.s['player'].update(weapon='weapon_hunting_rifle')
        self.s['player']['inventory']['slot0']='weapon_hunting_rifle'
        self.s['items']=[{'id':88,'type':'weapon_rifle_spawn','p':[70,0,0],'model':'models/w_models/weapons/w_rifle_m16a2.mdl'}]
        self.assertIn('loot_88',packet(self.s,self.nav,self.mem)[2])
    def test_remembered_ammo_is_revisited_before_another_trip_but_not_during_delivery(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['player']['weapons']['slot0'].update(type='weapon_rifle_ak47',clip=40,max_clip=40,reserve=81)
            ammo={'id':55,'type':'weapon_ammo_spawn','p':[500,0,0]}
            self.mem.remembered_items={55:{'item':ammo}}
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(planner.intent,ammo)
            ammo['p']=[1600,0,280]
            planner.update(self.s);self.assertIsNone(planner.intent)
            self.s['player']['weapons']['slot0'].update(reserve=0,clip=39)
            planner.update(self.s);self.assertEqual(planner.intent,ammo)
            data,_,tasks=packet(self.s,self.nav,self.mem)
            self.assertIn('ammunition pile',tasks['route'].description)
            self.assertTrue(tasks['route'].critical)
            self.assertEqual(data['route']['goal']['distance'],round(distance(self.s['player']['p'],ammo['p'])))
            self.assertTrue(data['route']['goal']['requires_reacquisition'])
            self.s['player'].update(weapon='weapon_cola_bottles')
            planner.update(self.s);self.assertIsNone(planner.intent)
            self.assertIsNone(self.mem.navigation_goal)
    def test_holdout_defense_does_not_require_an_unrelated_travel_route(self):
        self.s['map']='c2m5_concert';self.s['metrics']['flags']={'finale_start':True}
        with patch.object(self.nav,'waypoint',side_effect=NoRouteError('unrelated destination')) as waypoint:
            tasks=packet(self.s,self.nav,self.mem)[2]
            self.assertIn('hold',tasks);self.assertNotIn('route',tasks);waypoint.assert_not_called()
            self.s['metrics']['flags']['finale_vehicle_ready']=True
            with self.assertRaises(NoRouteError):packet(self.s,self.nav,self.mem)
    def test_confirmed_finale_start_finishes_use_and_suppresses_repeated_activation(self):
        self.s['map']='c2m5_concert'
        control={'id':38,'type':'trigger_finale','name':'stage_escape_button','p':[60,0,64],'disabled':False}
        self.s['interactables']=[control]
        self.motor._start(Task('use_38','use','Begin the rescue finale.',control),copy.deepcopy(self.s))
        self.assertIn('use_38',packet(self.s,self.nav,self.mem)[2])
        self.s['t']+=1;self.s['metrics']['flags']={'finale_start':True}
        self.motor._verify(self.s)
        self.assertIsNone(self.motor.task)
        self.assertEqual(self.mem.outcomes[-1]['status'],'input_accepted')
        self.assertNotIn('use_38',packet(self.s,self.nav,self.mem)[2])
        self.assertEqual(self.s['outcome'],'active')
    def test_missing_mission_route_does_not_block_urgent_tank_defense(self):
        self.s['enemies']=[enemy(kind=8,p=[300,0,64])]
        with patch.object(self.nav,'waypoint',side_effect=NoRouteError('mission route unavailable')):
            data,questions,tasks=packet(self.s,self.nav,self.mem)
            self.assertEqual(set(tasks),{'evade'})
            self.assertIn('fire',questions['combat']['criteria'])
            self.assertEqual(data['route']['link_type'],'unavailable')
            self.s['enemies']=[]
            with self.assertRaises(NoRouteError):packet(self.s,self.nav,self.mem)
    def test_involuntary_flight_waits_for_landing_when_mission_route_is_unavailable(self):
        with patch.object(self.nav,'waypoint',side_effect=NoRouteError('between floors')):
            self.s['player']['immobilized']=True
            self.assertEqual(set(packet(self.s,self.nav,self.mem)[2]),{'hold'})
            self.s['player'].update(immobilized=False,velocity=[0,0,-120])
            self.assertEqual(set(packet(self.s,self.nav,self.mem)[2]),{'hold'})
            self.s['player']['velocity']=[0,0,0]
            with self.assertRaises(NoRouteError):packet(self.s,self.nav,self.mem)
    def test_escape_route_uses_current_observed_obstructions(self):
        nav=Navigation.__new__(Navigation)
        nav.goal=4;nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.active_ladder=None;nav.cached_start=None;nav.path=[]
        nav.areas={i:{'id':i,'p':[x,0,0],'nw':[x-10,-10,0],'se':[x+10,10,0],
            'flow':i*10,'attr':0,'adj':adj} for i,x,adj in [(1,0,[2,4]),(2,200,[3]),(3,400,[]),(4,700,[])]}
        self.s['nearby_nav']=[{'id':i,'blocked':i==2,'damaging':False} for i in range(1,5)]
        # The nearer clear destination is now behind an observed blocked
        # intermediate area. Choose the longer open route without failing.
        step=SkillMotor(nav,self.mem).safe_escape(self.s)
        self.assertEqual(step['goal_area'],4)
    def test_batch_routes_do_not_use_non_survivor_goal_as_a_shortcut(self):
        nav=Navigation.__new__(Navigation)
        nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.areas={i:{'id':i,'p':[x,0,0],'nw':[x-10,-10,0],'se':[x+10,10,0],
            'flow':-1 if i==2 else i*10,'attr':0,'adj':adj}
            for i,x,adj in [(1,0,[2,4]),(2,200,[3]),(3,400,[]),(4,700,[])]}
        paths=nav.routes_to(1,[2,3,4])
        self.assertEqual(paths,{2:[1,2],4:[1,4]})
    def test_descending_elevator_waits_when_assigned_the_lower_floor_nav_area(self):
        nav=Navigation('c1m4_atrium');nav.goal=5822
        self.s['player'].update(p=[-4026.3,-3374.86,92.7],area=51)
        self.s['interactables']=[{'type':'func_elevator','p':[-4043,-3404,90]}]
        step=nav.waypoint(self.s)
        self.assertEqual(step['link_type'],'elevator')
        self.assertEqual(step['p'],[-4043,-3404,92.7])
        self.s['interactables']=[]
        with self.assertRaises(RuntimeError):nav.waypoint(self.s)
    def test_thin_doorway_aligns_sideways_before_advancing_into_frame(self):
        nav=Navigation.__new__(Navigation);nav.goal=2;nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.active_ladder=None;nav.cached_start=None;nav.path=[]
        nav.areas={1:{'id':1,'p':[1737.5,2712.5,2],'nw':[1725,2700,4],'se':[1750,2725,0],
            'flow':284,'attr':0,'adj':[2]},2:{'id':2,'p':[1762.5,2725,0],'nw':[1750,2700,0],
            'se':[1775,2750,0],'flow':312,'attr':0,'adj':[]}}
        self.s['player'].update(p=[1725.97,2704.97,4],area=1)
        waypoint=nav.waypoint(self.s)
        self.assertEqual(waypoint['p'][0],1725.97)
        self.assertEqual(waypoint['p'][1],2712.5)
        self.assertTrue(waypoint['align_portal'])
    def test_corner_outside_corridor_aligns_into_it_before_inward_step(self):
        nav=Navigation.__new__(Navigation);nav.goal=2;nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.active_ladder=None;nav.cached_start=None;nav.path=[]
        nav.areas={1:{'id':1,'p':[25,12.5,0],'nw':[0,0,0],'se':[50,25,0],
            'flow':0,'attr':0,'adj':[2]},2:{'id':2,'p':[62.5,12.5,0],'nw':[50,0,0],
            'se':[75,25,0],'flow':50,'attr':0,'adj':[1]}}
        self.s['player'].update(p=[46,-1.3,0],area=1)
        waypoint=nav.waypoint(self.s)
        self.assertEqual(waypoint['p'][0],46)
        self.assertEqual(waypoint['p'][1],12.5)
        self.assertTrue(waypoint['align_portal'])
    def test_observed_doorframe_block_uses_clear_lateral_alignment_first(self):
        nav=Navigation('c5m4_quarter');nav.avoided=set();s=state();s['map']=nav.map
        s['player'].update(area=2525,p=[-1050.9131,-1071.9688,98.0313],angles=[4.984,-112.3409,0])
        s['obstacles']=[{'angle':30,'fraction':.1009,'hit_type':'worldspawn'},
                        {'angle':-60,'fraction':.7016,'hit_type':'worldspawn'}]
        step=nav.waypoint(s,931)
        self.assertTrue(step['align_portal']);self.assertLess(step['p'][0],-1068)
        self.assertEqual(step['p'][1],s['player']['p'][1])
        # A blocked alternative is not permission for an unobserved sidestep.
        s['obstacles'][1]['fraction']=.1;step=nav.waypoint(s,931)
        self.assertEqual(step['p'][0],s['player']['p'][0]);self.assertLess(step['p'][1],-1080)
    def test_finale_starting_checkpoint_is_not_a_completed_safe_room(self):
        from agent_policy import tasks_for
        self.s.update(map='c1m4_atrium',exit_checkpoint_occupied=True)
        self.nav.goal=1;self.nav.areas[1]['spawn']=2048
        self.s['items']=[{'id':91,'type':'prop_door_rotating_checkpoint','door_state':2,'p':[60,0,0],'owned':False}]
        self.assertEqual(mission(self.s)['phase'],'travel')
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertIn('route',tasks)
        self.assertNotIn('use_91',tasks)
    def test_engine_incapacitated_tank_is_not_an_active_target_or_retreat_trigger(self):
        from observe_game import filter_inactive_enemies
        self.s['enemies']=[dict(enemy(20,8,[100,0,64]),incap=True),enemy(21,0,[500,0,64])]
        s=filter_inactive_enemies(self.s);data,questions,tasks=packet(s,self.nav,self.mem)
        self.assertEqual(s['inactive_enemies'][0]['id'],20)
        self.assertNotIn('evade',tasks)
        self.assertNotIn('enemy_20',questions['target']['criteria'])
        self.assertIn('enemy_21',questions['target']['criteria'])
        self.assertIsNone(self.motor.tick(s,choice(target=20))['target_id'])
        with patch.object(self.motor,'safe_escape',side_effect=AssertionError('An old Tank decision must not keep retreating')):
            self.assertEqual(self.motor.tick(s,choice('evade',20))['task'],'hold')
        self.assertEqual(self.motor.tick(s,choice('route',21))['task'],'route')
    def test_late_escape_choice_does_not_seek_more_escape_after_hazard_clears(self):
        with patch.object(self.motor,'safe_escape',side_effect=AssertionError('No current hazard')):
            self.assertEqual(self.motor.tick(self.s,choice('escape',None,'hold'))['task'],'hold')
    def test_new_retreat_is_not_declared_blocked_by_time_spent_in_previous_pickup(self):
        for i in range(30):
            self.s['t']=100+i;self.mem.update(copy.deepcopy(self.s))
        self.motor.task=Task('evade','evade','Tank just arrived');self.motor.started=129
        self.assertIsNone(progress_reason(self.s,self.mem,self.motor))
        for i in range(1,5):
            self.s['t']=129+i;self.mem.update(copy.deepcopy(self.s))
        self.assertEqual(progress_reason(self.s,self.mem,self.motor),'Tank retreat is blocked')
    def test_blocked_retreat_can_change_destination_when_original_has_no_alternate_path(self):
        nav=Navigation.__new__(Navigation)
        nav.goal=3;nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.active_ladder=None;nav.cached_start=None;nav.path=[]
        nav.areas={i:{'id':i,'p':[x,y,0],'nw':[x-10,y-10,0],'se':[x+10,y+10,0],
            'flow':i*10,'attr':0,'adj':adj} for i,x,y,adj in [(1,0,0,[2,4]),(2,-100,0,[3]),(3,-300,0,[]),(4,-200,200,[])]}
        self.s['nearby_nav']=[{'id':i,'blocked':False,'damaging':False} for i in nav.areas]
        self.s['enemies']=[enemy(kind=8,p=[100,0,64])]
        motor=SkillMotor(nav,self.mem);motor.escape_goal=3
        result=motor.recover_escape(self.s)
        self.assertEqual(result['edge'],[1,2])
        self.assertEqual(result['alternate_goal'],4)
        self.assertEqual(nav.path,[1,4])
        # If every other candidate is hazardous, do not invent a route or
        # keep a rejected graph change that has no verified alternative.
        nav.avoided.clear();nav.cached_start=None;motor.escape_goal=3
        self.s['nearby_nav'][-1]['damaging']=True
        self.assertIsNone(motor.recover_escape(self.s))
        self.assertNotIn((1,2),nav.avoided)
        self.assertEqual(motor.escape_goal,3)
    def test_missing_medkit_is_filled_before_leaving_visible_supplies(self):
        from agent_policy import tasks_for
        self.s['player']['inventory'].pop('slot3')
        self.s['items']=[{'id':56,'type':'weapon_first_aid_kit_spawn','p':[400,0,0],'owned':False}]
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertEqual(set(tasks),{'loot_56'})
        self.s['player'].update(weapon='weapon_gascan',clip=-1)
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertNotIn('loot_56',tasks)
        self.assertIn('route',tasks)
    def test_stacked_floor_correction_uses_observed_height_and_hazard(self):
        nav=Navigation.__new__(Navigation)
        nav.areas={1:{'id':1,'nw':[-30,-30,172],'se':[30,30,172]},2:{'id':2,'nw':[5,-30,280],'se':[35,30,280]}}
        self.s['player'].update(p=[0,0,280],area=1)
        self.s['nearby_nav']=[{'id':2,'blocked':False,'damaging':True}]
        evidence=nav.correct_player_area(self.s)
        self.assertEqual(evidence['sensor_floor_error'],108)
        self.assertEqual(self.s['player']['area'],2)
        self.assertEqual(self.s['player']['sensor_area'],1)
        self.assertTrue(self.s['player']['area_damaging'])
        self.s['player'].update(area=1,velocity=[0,0,-80])
        self.assertIsNone(nav.correct_player_area(self.s))
        self.s['player'].update(velocity=[0,0,0],p=[100,0,280])
        self.assertIsNone(nav.correct_player_area(self.s))
    def test_tank_retreat_does_not_keep_gascan_to_aim_at_a_common(self):
        self.s['player'].update(weapon='weapon_gascan',clip=-1)
        self.s['player']['weapons']['slot0'].update(clip=0,reserve=0)
        self.s['enemies']=[enemy(20,8,[200,0,64]),enemy(21,0,[0,300,64])]
        action=self.motor.tick(self.s,choice(target=21))
        self.assertEqual(action['target_id'],20)
        self.assertEqual(action['task'],'evade')
        self.assertIn('2',action['keys'])
        self.assertNotIn('1',action['keys'])
    def test_tank_retreat_can_still_free_a_pinned_teammate_first(self):
        self.s['enemies']=[enemy(20,8,[200,0,64]),enemy(21,1,[300,100,64])]
        self.s['teammates']=[dict(bot(p=[300,110,0]),pinned=True)]
        action=self.motor.tick(self.s,choice(target=21))
        self.assertEqual(action['target_id'],21)
    def test_low_primary_refills_visible_ammo_before_leaving_for_another_can(self):
        from agent_policy import tasks_for
        self.s['player']['weapons']['slot0'].update(clip=3,reserve=0)
        self.s['items']=[{'id':55,'type':'weapon_ammo_spawn','p':[500,0,0],'owned':False}]
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertEqual(set(tasks),{'loot_55'})
        self.s['player']['weapon']='weapon_gascan';self.s['player']['clip']=-1
        tasks,_=tasks_for(self.s,self.nav,self.mem)
        self.assertNotIn('loot_55',tasks)
        self.assertIn('route',tasks)
    def test_fuel_carrier_faces_route_instead_of_distant_ordinary_enemy(self):
        self.s['player'].update(weapon='weapon_gascan',clip=-1)
        self.s['enemies']=[enemy(p=[0,500,64])]
        action=self.motor.tick(self.s,choice())
        self.assertEqual(action['dx'],0)
        self.assertEqual(action['buttons'],[])
        self.assertIn('w',action['keys'])
        self.s['enemies'][0]['type']=6
        action=self.motor.tick(self.s,choice())
        self.assertLess(action['dx'],0)
        self.assertIn('1',action['keys'])
    def test_distant_common_is_deferred_but_closing_and_special_threats_remain(self):
        self.s['enemies']=[enemy(p=[1200,0,64])]
        self.assertNotIn('enemy_20',packet(self.s,self.nav,self.mem)[1]['target']['criteria'])
        self.assertFalse(self.motor.tick(self.s,choice())['buttons'])
        self.s['enemies'][0]['p']=[600,0,64]
        self.assertIn('enemy_20',packet(self.s,self.nav,self.mem)[1]['target']['criteria'])
        self.s['enemies'][0].update(type=1,p=[1200,0,64])
        self.assertIn('enemy_20',packet(self.s,self.nav,self.mem)[1]['target']['criteria'])
    def test_long_rifle_shot_requires_alignment_after_bounded_mouse_correction(self):
        self.s['enemies']=[enemy(kind=8,p=[600,0,64])]
        self.s['player']['angles']=[0,70,0]
        self.assertNotIn('fire',self.motor.tick(self.s,choice())['buttons'])
        self.s['player']['angles']=[0,3,0];self.s['t']+=.1
        action=self.motor.tick(self.s,choice())
        self.assertIn('fire',action['buttons']);self.assertGreater(action['dx'],0)
    def test_cornered_tank_defense_keeps_fire_without_inventing_movement(self):
        self.s['enemies']=[enemy(kind=8,p=[200,0,64])]
        self.s['player']['angles']=[0,0,0]
        action=self.motor.tick(self.s,choice())
        self.assertTrue(action['waypoint']['escape_unavailable'])
        self.assertEqual(action['keys'],[])
        self.assertIn('fire',action['buttons'])
        for i in range(5):
            self.s['t']=100+i;self.mem.update(copy.deepcopy(self.s))
        self.assertEqual(progress_reason(self.s,self.mem,self.motor),'Tank retreat is blocked')
    def test_missing_hazard_escape_still_requires_visual_handoff(self):
        self.s['player']['area_damaging']=True
        with self.assertRaisesRegex(RuntimeError,'No validated nearby escape'):
            self.motor.tick(self.s,choice())
    def test_distant_cornered_defense_requires_damage_and_has_a_deadline(self):
        self.s['enemies']=[enemy(kind=8,p=[700,0,64])]
        self.s['enemies'][0]['health']=3000
        self.motor._start(Task('evade','evade','Retreat while defending.'),copy.deepcopy(self.s))
        for i in range(5):
            self.s['t']=100+i;self.s['metrics']['shots']=i*3
            self.s['enemies'][0]['health']=3000-i*100
            self.mem.update(copy.deepcopy(self.s))
        self.assertEqual(self.mem.recent_tank_damage,400)
        self.assertIsNone(progress_reason(self.s,self.mem,self.motor))
        self.s['enemies'][0]['p']=[500,0,64]
        self.assertEqual(progress_reason(self.s,self.mem,self.motor),'Tank retreat is blocked')
        self.s['enemies'][0]['p']=[700,0,64]
        for i in range(5,10):
            self.s['t']=100+i;self.s['metrics']['shots']=i*3
            self.mem.update(copy.deepcopy(self.s))
        self.assertEqual(self.mem.recent_tank_damage,0)
        self.assertEqual(progress_reason(self.s,self.mem,self.motor),'Tank retreat is blocked')
        for i in range(10,14):
            self.s['t']=100+i;self.s['metrics']['shots']=i*3
            self.s['enemies'][0]['health']-=100;self.mem.update(copy.deepcopy(self.s))
        self.assertGreater(self.mem.recent_tank_damage,0)
        self.assertEqual(progress_reason(self.s,self.mem,self.motor),'Tank retreat is blocked')
    def test_tank_escape_can_follow_stairs_below_a_higher_floor_threat(self):
        self.s['player'].update(p=[0,0,152],eye=[0,0,214])
        tank=enemy(kind=8,p=[0,-270,303]);self.s['enemies']=[tank]
        self.nav.areas={1:{'id':1,'p':[0,0,152]},2:{'id':2,'p':[0,-100,80]},3:{'id':3,'p':[0,-300,0]},4:{'id':4,'p':[0,-600,0]}}
        self.nav.route=lambda start,goal,**kw:list(range(1,goal+1))
        self.s['nearby_nav']=[{'id':i,'damaging':False,'blocked':False} for i in self.nav.areas]
        self.motor.safe_escape(self.s,[tank])
        self.assertEqual(self.motor.escape_goal,4)
    def test_rifle_trigger_stays_held_during_engine_fire_cooldown(self):
        self.s['player']['weapon']='weapon_rifle';self.s['player']['clip']=20
        self.s['player']['weapons']['slot0'].update(type='weapon_rifle',clip=20,next_attack=100.5)
        self.s['enemies']=[enemy(p=[500,0,64])]
        self.assertFalse(perceive(self.s,self.mem,self.nav.waypoint(self.s))['self']['ammo']['ready'])
        self.assertIn('fire',self.motor.tick(self.s,choice())['buttons'])
        self.s['t']+=.1
        self.assertIn('fire',self.motor.tick(self.s,choice())['buttons'])
        self.s['teammates']=[bot(p=[250,0,26])]
        self.assertNotIn('fire',self.motor.tick(self.s,choice())['buttons'])
    def test_pistol_pulses_between_fresh_observations_instead_of_once_per_second(self):
        self.s['player']['weapon']='weapon_pistol';self.s['player']['clip']=10
        self.s['player']['weapons']['slot0'].update(clip=0,reserve=0)
        self.s['player']['weapons']['slot1']={'type':'weapon_pistol','clip':10,'reserve':0,'next_attack':0}
        self.s['enemies']=[enemy(p=[500,0,64])]
        self.assertIn('fire',self.motor.tick(self.s,choice())['buttons'])
        self.s['t']+=.1;self.assertNotIn('fire',self.motor.tick(self.s,choice())['buttons'])
        self.s['t']+=.1;self.assertIn('fire',self.motor.tick(self.s,choice())['buttons'])
    def test_visible_charger_readies_gun_before_entering_charge_range(self):
        self.s['player']['weapon']='weapon_gascan';self.s['enemies']=[enemy(kind=6,p=[600,0,64])]
        action=self.motor.tick(self.s,choice())
        self.assertIn('1',action['keys']);self.assertFalse(action['buttons'])
        self.s['player']['weapon']='weapon_pumpshotgun';self.s['map']='c1m4_atrium'
        self.s['items']=[{'id':40,'type':'weapon_gascan','p':[50,0,10]}]
        self.assertNotIn('carry_40',packet(self.s,self.nav,self.mem)[2])
    def test_available_automatic_rifle_can_replace_short_range_shotgun(self):
        self.s['player']['inventory']['slot0']='weapon_shotgun_spas'
        self.s['items']=[{'id':40,'type':'weapon_rifle_ak47_spawn','p':[80,0,10]}]
        self.assertIn('loot_40',packet(self.s,self.nav,self.mem)[2])
    def test_throw_requires_held_fuel_and_stops_fire_when_released(self):
        spot={'id':'balcony','stage':True,'p':[0,0,0],'aim':[500,0,64],'landing_p':[500,0,8]}
        d={'task':Task('stage_balcony','stage','throw',spot),'target_id':None,'combat':'hold'}
        action=self.motor.tick(self.s,d)
        self.assertNotIn('fire',action['buttons'])
        self.s['player']['weapon']='weapon_gascan';self.s['t']+=1
        self.s['player']['weapons']['slot5']={'type':'weapon_gascan','id':42}
        action=self.motor.tick(self.s,d)
        self.assertNotIn('fire',action['buttons'])
        self.s['t']+=.3;action=self.motor.tick(self.s,d)
        self.assertEqual(action['buttons'],['fire']);self.assertFalse(action['keys'])
        self.s['player']['weapon']='weapon_pumpshotgun';self.s['t']+=.1
        self.assertFalse(self.motor.tick(self.s,d)['buttons'])
        self.s['t']+=.8
        self.assertFalse(self.motor.tick(self.s,d)['buttons'])
        result=self.mem.outcomes[-1]
        self.assertEqual(result['status'],'input_accepted')
        self.assertIn('unverified',result['evidence'])
        self.assertFalse(self.mem.allowed('carry_42',self.s['t']+1))
        self.assertTrue(self.mem.allowed('carry_42',self.s['t']+9))
        self.assertEqual(self.mem.released_fuel['42']['status'],'released')
        self.assertEqual(self.mem.released_fuel['42']['search_p'],[500,0,8])
    def test_fuel_inventory_excludes_held_entity_even_if_engine_owner_is_null(self):
        from fuel_planner import FuelPlanner
        self.s['player']['weapon']='weapon_gascan'
        self.s['player']['weapons']['slot5']={'type':'weapon_gascan','id':42}
        self.s['items']=[{'id':42,'type':'weapon_gascan','owned':False,'p':[10,0,60]}]
        with tempfile.TemporaryDirectory() as tmp:
            planner=FuelPlanner(Path(tmp),self.nav);planner.update(self.s,self.mem)
            self.assertFalse(planner.data['cans'])
    def test_throw_animation_keeps_aim_when_bumped_beyond_arrival_radius(self):
        self.s['player']['weapon']='weapon_gascan'
        spot={'id':'balcony','stage':True,'p':[0,0,0],'aim':[500,0,64]}
        d={'task':Task('stage_balcony','stage','throw',spot),'target_id':20,'combat':'fire'}
        self.s['enemies']=[enemy(p=[100,300,64])]
        self.motor.tick(self.s,d);self.s['t']+=.3
        self.assertIn('fire',self.motor.tick(self.s,d)['buttons'])
        self.s['player']['p']=[-70,0,0];self.s['player']['eye']=[-70,0,64];self.s['t']+=.1
        action=self.motor.tick(self.s,d)
        self.assertFalse(action['keys']);self.assertEqual(action['dx'],0)
    def test_throw_point_is_reached_before_fire_and_not_stopped_short(self):
        self.s['player']['weapon']='weapon_gascan'
        spot={'id':'balcony','stage':True,'p':[60,0,0],'aim':[500,0,200]}
        self.nav.closest_area=lambda p:1
        action=self.motor.tick(self.s,{'task':Task('stage_balcony','stage','throw',spot),'target_id':None,'combat':'hold'})
        self.assertIn('w',action['keys']);self.assertNotIn('fire',action['buttons'])
    def test_narrow_throw_opening_requires_precise_arrival(self):
        self.s['player']['weapon']='weapon_gascan'
        self.s['player'].update(p=[-25,0,0],eye=[-25,0,64])
        spot={'id':'balcony','stage':True,'p':[0,0,0],'aim':[500,0,64]}
        self.nav.closest_area=lambda p:1
        d={'task':Task('stage_balcony','stage','throw',spot),'target_id':None,'combat':'hold'}
        self.motor.tick(self.s,d);self.s['t']+=.3
        action=self.motor.tick(self.s,d)
        self.assertIn('w',action['keys']);self.assertNotIn('fire',action['buttons'])
    def test_prethrow_drift_requires_repositioning_before_release(self):
        self.s['player']['weapon']='weapon_gascan'
        self.s['player'].update(p=[-6,0,0],eye=[-6,0,64])
        spot={'id':'balcony','stage':True,'p':[0,0,0],'aim':[500,0,64]}
        self.nav.closest_area=lambda p:1
        d={'task':Task('stage_balcony','stage','throw',spot),'target_id':None,'combat':'hold'}
        self.motor.tick(self.s,d)
        self.s['player'].update(p=[-54,0,0],eye=[-54,0,64]);self.s['t']+=.3
        action=self.motor.tick(self.s,d)
        self.assertIn('w',action['keys']);self.assertNotIn('fire',action['buttons'])
    def test_staging_pickups_exclude_ground_cans_but_preserve_tank_escape(self):
        self.s['map']='c1m4_atrium';self.mem.fuel_stage_upper=True
        self.s['player'].update(p=[0,0,280],eye=[0,0,344])
        self.s['items']=[{'id':30,'type':'weapon_gascan','p':[50,0,12]}, {'id':31,'type':'weapon_gascan','p':[50,0,292]}]
        _,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertNotIn('carry_30',tasks);self.assertIn('carry_31',tasks)
        self.s['enemies']=[enemy(kind=8)]
        self.assertEqual(set(packet(self.s,self.nav,self.mem)[2]),{'evade'})
    def test_fuel_staging_only_counts_observed_lower_cans_and_is_round_scoped(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['player']['p']=[0,0,280];self.s['metrics']['flags']={'finale_start':True}
            root.joinpath('fuel-search-sites.json').write_text(json.dumps({'c1m4_atrium':[{'id':'upper','p':[900,0,292]}]}))
            config={'enabled':True,'map':self.s['map'],'round_id':'old','observer':'Astra visual review in current thread','reviewed_image':'reviewed.png','required_cans':2,'spots':[{'id':'balcony','stage':True,'p':[0,0,280],'aim':[500,0,450]}]}
            root.joinpath('fuel-staging-config.json').write_text(json.dumps(config))
            planner=FuelPlanner(root,self.nav);planner.update(self.s,self.mem)
            self.assertFalse(self.mem.fuel_stage_upper)
            planner.staging['round_id']='current'
            planner.update(self.s,self.mem);self.assertTrue(self.mem.fuel_stage_upper)
            self.mem.outcome('stage_balcony','input_accepted',100,'released')
            planner.update(self.s,self.mem);self.assertFalse(planner.data.get('staging_complete'))
            self.s['items']=[{'id':30,'type':'weapon_gascan','p':[200,0,12]}, {'id':31,'type':'weapon_gascan','p':[240,0,12]}]
            planner.update(self.s,self.mem)
            self.assertTrue(planner.data['staging_complete']);self.assertFalse(self.mem.fuel_stage_upper)
    def test_released_fuel_survives_handoff_without_chasing_its_midair_position(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['player']['p']=[0,0,280];self.s['metrics']['flags']={'finale_start':True}
            root.joinpath('fuel-search-sites.json').write_text(json.dumps({'c1m4_atrium':[{'id':'upper','p':[900,0,292]}]}))
            config={'enabled':True,'map':self.s['map'],'round_id':'current','observer':'Astra visual review in current thread','reviewed_image':'reviewed.png','required_cans':8,'spots':[{'id':'balcony','stage':True,'p':[0,0,280],'aim':[500,0,450]}]}
            root.joinpath('fuel-staging-config.json').write_text(json.dumps(config))
            self.mem.released_fuel={'42':{'t':self.s['t'],'search_p':[500,0,8],'status':'released'}}
            self.s['items']=[{'id':42,'type':'weapon_gascan','p':[200,0,250]}]
            planner=FuelPlanner(root,self.nav)
            self.assertEqual(planner.update(self.s,self.mem)['id'],'upper')
            planner.save(self.s['t']);self.s['items']=[];self.s['t']+=2
            restored=FuelPlanner(root,self.nav)
            self.assertEqual(restored.update(self.s,EvidenceMemory())['id'],'upper')
            self.assertEqual(restored.data['released']['42']['status'],'released')
    def test_expected_fuel_changes_search_floor_without_claiming_a_landing(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['player']['p']=[0,0,280];self.s['metrics'].update(flags={'finale_start':True},pours=1)
            config={'enabled':True,'map':self.s['map'],'round_id':'current','observer':'Astra visual review in current thread','reviewed_image':'reviewed.png','required_cans':2,'spots':[{'id':'balcony','stage':True,'p':[0,0,280],'aim':[500,0,450]}]}
            root.joinpath('fuel-staging-config.json').write_text(json.dumps(config))
            self.mem.released_fuel={'42':{'t':self.s['t'],'search_p':[500,0,8],'status':'released'}}
            planner=FuelPlanner(root,self.nav);target=planner.update(self.s,self.mem)
            self.assertEqual(target['type'],'search');self.assertEqual(target['id'],'landing_42')
            self.assertFalse(self.mem.fuel_stage_upper);self.assertFalse(planner.data.get('staging_complete'))
            self.s['items']=[{'id':42,'type':'weapon_gascan','p':[510,0,8]}]
            self.s['t']+=1;planner.update(self.s,self.mem)
            self.s['t']+=.2;planner.update(self.s,self.mem)
            self.assertFalse(planner.data.get('staging_complete'))
            self.s['t']+=.6;planner.update(self.s,self.mem)
            self.assertEqual(planner.data['released']['42']['status'],'landed')
            self.assertEqual(planner.data['released']['42']['landing_p'],[510,0,8])
            self.assertTrue(planner.data['staging_complete'])
            self.s['t']+=1;planner.update(self.s,self.mem)
            self.assertEqual(planner.data['released']['42']['status'],'landed')
    def test_new_round_discards_previous_throw_search(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='new'
            root.joinpath('fuel-plan-state.json').write_text(json.dumps({'map':self.s['map'],'round_id':'old','game_t':self.s['t']-1,'cans':{},'searched':[],'processed_pickups':[],'released':{'42':{'t':1,'search_p':[500,0,8],'status':'released'}}}))
            planner=FuelPlanner(root,self.nav);planner.update(self.s,self.mem)
            self.assertFalse(planner.data['released'])
    def test_observed_elevator_between_nav_floors_waits_without_movement(self):
        self.s['player'].update(area=None,p=[-3992.2988,-3450.5071,388.6979])
        self.s['interactables']=[{'type':'func_elevator','p':[-4043,-3404,386.0002]}]
        self.assertTrue(wait_for_elevator(self.s,10))
        self.assertFalse(wait_for_elevator(self.s,45))
        self.s['player']['p'][2]+=100
        self.assertFalse(wait_for_elevator(self.s,10))
        self.s['player']['p']=[-3800,-3404,386]
        self.assertFalse(wait_for_elevator(self.s,10))
    def test_evade_recovery_replans_the_escape_goal_not_the_campaign_exit(self):
        nav=Navigation.__new__(Navigation);nav.avoided=set();nav.cached_start=None;nav.map='c1m4_atrium';nav.path=[206,506,1729]
        with tempfile.TemporaryDirectory() as tmp:
            nav.recovery_file=Path(tmp)/'recoveries.jsonl'
            with patch.object(nav,'waypoint') as waypoint,patch.object(nav,'route',return_value=[206,27926,1729]) as route:
                self.assertTrue(nav.avoid_current_edge(self.s,'Tank retreat blocked',1729,escaping=True))
                waypoint.assert_called_once_with(self.s,1729,escaping=True)
                route.assert_called_once_with(self.s['player']['area'],1729,escaping=True)
        self.assertIn((206,506),nav.avoided)
    def test_arrived_fuel_delivery_does_not_offer_already_finished_travel(self):
        self.s['map']='c1m4_atrium';self.s['player']['weapon']='weapon_gascan'
        self.s['interactables']=[{'id':360,'type':'point_prop_use_target','p':[30,0,64],'visible':True}]
        _,questions,tasks=packet(self.s,self.nav,self.mem)
        self.assertIn('deliver_360',tasks);self.assertNotIn('route',tasks)
        self.assertIn('30 units',questions['task']['criteria']['deliver_360'])
    def test_stale_second_pickup_cannot_confirm_an_uncollected_can(self):
        self.s['player']['weapon']='weapon_gascan';self.s['player']['inventory']['slot5']='weapon_gascan'
        task=Task('carry_358','carry','collect',{'id':358,'p':[30,0,0]})
        action=self.motor.tick(self.s,{'task':task,'target_id':None,'combat':'hold'})
        self.assertEqual(action['task'],'route')
        self.assertFalse(any(o['task']=='carry_358' and o['status']=='verified' for o in self.mem.outcomes))
    def test_nearby_can_pickup_confirms_actual_entity_not_requested_neighbor(self):
        task=Task('carry_137','carry','collect',{'id':137,'p':[30,0,0]})
        self.motor._start(task,copy.deepcopy(self.s))
        self.s['player']['weapon']='weapon_gascan';self.s['player']['weapons']['slot5']={'id':140,'type':'weapon_gascan'}
        self.motor._verify(self.s)
        self.assertTrue(any(o['task']=='carry_140' and o['status']=='verified' for o in self.mem.outcomes))
        self.assertFalse(any(o['task']=='carry_137' and o['status']=='verified' for o in self.mem.outcomes))
    def test_consumed_can_cannot_remain_a_collection_destination(self):
        from fuel_planner import FuelPlanner
        self.s['items']=[{'id':140,'type':'weapon_gascan','p':[50,0,20]}];self.mem.consumed_fuel={140}
        with tempfile.TemporaryDirectory() as tmp:
            planner=FuelPlanner(Path(tmp),self.nav);planner.update(self.s,self.mem)
            self.assertNotIn('140',planner.data['cans'])
    def test_new_round_discards_fuel_search_history_even_if_game_clock_increases(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            root.joinpath('fuel-plan-state.json').write_text(json.dumps({'map':self.nav.map,'round_id':'old','game_t':99,'cans':{'10':{'item':{'id':10,'p':[999,0,0]}}},'searched':['old-site'],'processed_pickups':['carry_10:90']}))
            self.s['round_id']='new'
            planner=FuelPlanner(root,self.nav);planner.update(self.s,self.mem)
            self.assertFalse(planner.data['cans']);self.assertFalse(planner.data['searched']);self.assertFalse(planner.data['processed_pickups'])
            self.assertEqual(planner.data['round_id'],'new')
    def test_new_round_rejects_old_planner_directive_with_matching_game_clock(self):
        d={'version':'old','map':self.s['map'],'round_id':'old','created_game_t':90,'expires_game_t':120,'public_instruction':'old objective'}
        self.s['round_id']='new'
        self.assertFalse(valid_directive(d,self.s,self.nav))
        d['round_id']='new';self.assertTrue(valid_directive(d,self.s,self.nav))
    def test_low_health_uses_available_boost_before_nonurgent_travel(self):
        self.s['player'].update(health=1,temp_health=37)
        self.s['player']['inventory']['slot4']='weapon_adrenaline'
        _,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertEqual(set(tasks),{'boost'})
        self.s['enemies']=[enemy(kind=8)]
        _,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertEqual(set(tasks),{'evade'})
    def test_near_common_blocker_can_be_shoved_between_jev_decisions(self):
        self.s['enemies']=[enemy(p=[60,0,64])]
        action=self.motor.tick(self.s,choice('route',None,'hold'))
        self.assertIn('shove',action['buttons'])
    def test_low_confidence_valid_pour_is_attempted_with_outcome_checks(self):
        task=Task('deliver_360','deliver','pour',{'id':360,'p':[70,0,64]},critical=True)
        result={'answers':{k:{'choice':v,'confidence':.33} for k,v in [('task',task.key),('target','none'),('combat','hold')]}}
        decision=compose(result,{task.key:task},self.s)
        self.assertFalse(decision['needs_review']);self.assertEqual(decision['task'].kind,'deliver')
    def test_facing_close_enemy_does_not_throw_fuel_toward_distant_target(self):
        self.s['player']['weapon']='weapon_gascan';self.s['player']['clip']=-1
        item={'id':360,'type':'point_prop_use_target','p':[130,30,64]};self.s['interactables']=[item]
        self.s['enemies']=[dict(enemy(p=[180,0,64]),velocity=[-1000,0,0])]
        d={'task':Task('deliver_360','deliver','pour',item),'combat':'fire','target_id':20}
        # Predicted enemy aim is100units away; the actual pour target remains
        # out of reach. The former check fired at that predicted enemy point.
        action=self.motor.tick(self.s,d)
        self.assertNotIn('fire',action['buttons'])
    def test_unconfirmed_deliveries_count_toward_failure_handoff(self):
        self.mem.outcome('deliver_360','needs_confirmation',100,'can gone without pour')
        self.mem.outcome('deliver_360','needs_confirmation',120,'can gone without pour')
        self.assertEqual(self.mem.failures['deliver_360'],2)
    def test_pour_waits_for_close_range_precise_aim_and_carried_can(self):
        self.s['player']['weapon']='weapon_gascan';self.s['player']['clip']=-1
        item={'id':360,'type':'point_prop_use_target','p':[85,0,64]};self.s['interactables']=[item]
        d={'task':Task('deliver_360','deliver','pour',item),'combat':'hold','target_id':None}
        self.assertNotIn('fire',self.motor.tick(self.s,d)['buttons'])
        item['p']=[60,0,64];self.s['player']['angles'][1]=8
        self.assertNotIn('fire',self.motor.tick(self.s,d)['buttons'])
        self.s['player']['angles'][1]=0
        self.assertIn('fire',self.motor.tick(self.s,d)['buttons'])
        self.s['player']['weapon']='weapon_pumpshotgun';self.s['t']+=.1
        self.assertNotIn('fire',self.motor.tick(self.s,d)['buttons'])
    def test_occluded_nearby_fuel_port_keeps_approaching_instead_of_freezing(self):
        self.s['player']['weapon']='weapon_gascan';self.s['player']['clip']=-1
        item={'id':360,'type':'point_prop_use_target','p':[60,0,64],'visible':False}
        self.s['interactables']=[item]
        d={'task':Task('deliver_360','deliver','pour',item),'combat':'hold','target_id':None}
        action=self.motor.tick(self.s,d)
        self.assertTrue(set(action['keys']) & {'w','a','s','d'})
        self.assertIsNotNone(action['waypoint']);self.assertNotIn('fire',action['buttons'])
    def test_reacquired_nearby_fuel_port_stops_and_pours(self):
        self.s['player']['weapon']='weapon_gascan';self.s['player']['clip']=-1
        item={'id':360,'type':'point_prop_use_target','p':[60,0,64],'visible':False}
        self.s['interactables']=[item]
        d={'task':Task('deliver_360','deliver','pour',item),'combat':'hold','target_id':None}
        self.motor.tick(self.s,d)
        item['visible']=True;self.s['t']+=.1
        action=self.motor.tick(self.s,d)
        self.assertEqual(action['keys'],[]);self.assertIn('fire',action['buttons'])
    def test_late_delivery_decision_cannot_prevent_defense_after_can_lost(self):
        item={'id':360,'type':'point_prop_use_target','p':[70,0,64]};self.s['interactables']=[item]
        self.s['enemies']=[enemy(p=[300,0,64])]
        d={'task':Task('deliver_360','deliver','pour',item),'combat':'fire','target_id':20}
        action=self.motor.tick(self.s,d)
        self.assertEqual(action['task'],'hold');self.assertIn('fire',action['buttons'])
    def test_covered_common_does_not_cancel_pour_but_real_damage_does(self):
        self.s['player']['weapon']='weapon_gascan';self.s['player']['clip']=-1
        item={'id':360,'type':'point_prop_use_target','p':[70,0,64]};self.s['interactables']=[item]
        self.s['enemies']=[enemy(p=[150,0,64])]
        d={'task':Task('deliver_360','deliver','pour',item),'combat':'hold','target_id':None}
        self.assertIn('fire',self.motor.tick(self.s,d)['buttons'])
        self.mem.recent_damage=9;self.s['t']+=.1
        self.assertNotIn('fire',self.motor.tick(self.s,d)['buttons'])
    def test_unrelated_inventory_change_does_not_verify_fuel_pickup(self):
        item={'id':244,'type':'weapon_gascan','p':[70,0,4]};self.s['items']=[item]
        self.motor._start(Task('carry_244','carry','pickup fuel',item),self.s)
        s=copy.deepcopy(self.s);s['t']+=1;s['player']['inventory']['slot4']='weapon_pain_pills'
        self.motor._verify(s);self.assertFalse(self.mem.outcomes)
    def test_fuel_pickup_is_retired_and_actual_dropped_can_reacquired(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            self.nav.map='c1m4_atrium';self.s['map']=self.nav.map;self.s['metrics']['flags']={'finale_start':True}
            planner=FuelPlanner(Path(directory),self.nav)
            can={'id':244,'type':'weapon_gascan','p':[300,0,4],'owned':False};self.s['items']=[can]
            self.assertEqual(planner.update(self.s,self.mem)['id'],244)
            self.s['t']+=1;self.s['player']['weapon']='weapon_gascan';self.s['items']=[dict(can,owned=True)]
            self.mem.outcome('carry_244','verified',self.s['t'],'held actual fuel')
            self.assertIsNone(planner.update(self.s,self.mem));self.assertNotIn('244',planner.data['cans'])
            self.s['t']+=2;self.s['player']['weapon']='weapon_pumpshotgun';self.s['items']=[dict(can,p=[500,0,4])]
            self.assertEqual(planner.update(self.s,self.mem)['p'],[500,0,4])
    def test_unseen_fuel_search_location_never_becomes_pickup_task(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.nav.map='c1m4_atrium';self.s['map']=self.nav.map
            (root/'fuel-search-sites.json').write_text(json.dumps({self.nav.map:[{'id':'map-site','p':[900,0,0]}]}))
            self.s['metrics']['flags']={'finale_start':True}
            planner=FuelPlanner(root,self.nav);self.assertEqual(planner.update(self.s,self.mem)['source'],'search location')
            _,_,tasks=packet(self.s,self.nav,self.mem)
            self.assertFalse(any(task.kind=='carry' for task in tasks.values()))
    def test_fuel_pickup_inside_save_throttle_is_persisted_on_a_quiet_frame(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['metrics']['flags']={'finale_start':True}
            self.s['items']=[{'id':244,'type':'weapon_gascan','p':[300,0,4],'owned':False}]
            planner=FuelPlanner(Path(directory),self.nav);planner.update(self.s,self.mem)
            self.s['t']+=.1;self.s['items']=[]
            self.mem.outcome('carry_244','verified',self.s['t'],'held actual fuel')
            planner.update(self.s,self.mem)
            self.assertTrue(planner.dirty);self.assertNotIn('244',planner.data['cans'])
            self.s['t']+=1;planner.update(self.s,self.mem)
            restored=FuelPlanner(Path(directory),self.nav)
            self.assertNotIn('244',restored.data['cans']);self.assertFalse(planner.dirty)
    def test_fuel_search_competes_with_distant_known_cans_but_not_nearby_ones(self):
        from fuel_planner import FuelPlanner
        for can_x,site_x,expected in ((900,250,'near-site'),(200,900,244)):
            with self.subTest(can_x=can_x),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);self.s['map']=self.nav.map='c1m4_atrium'
                self.s['metrics']['flags']={'finale_start':True}
                self.s['items']=[{'id':244,'type':'weapon_gascan','p':[can_x,0,4],'owned':False}]
                self.s['interactables']=[{'id':360,'type':'point_prop_use_target','p':[0,0,64]}]
                root.joinpath('fuel-search-sites.json').write_text(json.dumps({self.nav.map:[{'id':'near-site','p':[site_x,0,4]}]}))
                with patch.object(self.nav,'closest_area',side_effect=lambda p,**kw:1 if p[0]<100 else 2 if p[0]<500 else 3):
                    goal=FuelPlanner(root,self.nav).update(self.s,self.mem)
                self.assertEqual(goal['id'],expected)
    def test_fuel_selection_includes_the_return_trip_to_the_observed_car(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            self.s['map']=self.nav.map='c1m4_atrium';self.s['metrics']['flags']={'finale_start':True}
            self.s['items']=[{'id':20,'type':'weapon_gascan','p':[200,0,4]},{'id':30,'type':'weapon_gascan','p':[900,0,4]}]
            self.s['interactables']=[{'id':360,'type':'point_prop_use_target','p':[0,0,64]}]
            self.nav.areas[4]={'id':4,'p':[3000,0,0]}
            def route(a,b,**kw):return [2,4,1] if (a,b)==(2,1) else [a,b]
            with patch.object(self.nav,'closest_area',side_effect=lambda p,**kw:1 if p[0]<100 else 2 if p[0]<500 else 3),patch.object(self.nav,'route',side_effect=route):
                goal=FuelPlanner(Path(directory),self.nav).update(self.s,self.mem)
            self.assertEqual(goal['id'],30)
    def test_staging_search_requires_a_reviewed_throw_floor(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['metrics']['flags']={'finale_start':True}
            config={'enabled':True,'map':self.s['map'],'round_id':'current',
                'observer':'Astra visual review in current thread','reviewed_image':'actual.png','required_cans':8,
                'spots':[{'id':'south','p':[400,0,280],'aim':[500,0,440],'landing_p':[500,0,8]}]}
            root.joinpath('fuel-staging-config.json').write_text(json.dumps(config))
            root.joinpath('fuel-search-sites.json').write_text(json.dumps({self.nav.map:[{'id':'second-floor','p':[200,0,284]}]}))
            self.s['items']=[{'id':99,'type':'weapon_gascan','p':[60,0,540]}]
            planner=FuelPlanner(root,self.nav)
            self.assertEqual(planner.update(self.s,self.mem)['id'],'second-floor')
            _,_,tasks=packet(self.s,self.nav,self.mem)
            self.assertNotIn('carry_99',tasks)
            self.s['player']['weapon']='weapon_gascan';self.s['player']['p']=[400,0,280]
            planner.update(self.s,self.mem)
            self.assertEqual(self.mem.staging_target['id'],'south')
    def test_staging_can_wait_for_actual_ground_pours(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['metrics'].update(flags={'finale_start':True},pours=2)
            config={'enabled':True,'map':self.s['map'],'round_id':'current',
                'observer':'Astra visual review in current thread','reviewed_image':'actual.png','required_cans':8,
                'min_pours_before_staging':3,'spots':[{'p':[400,0,280]}]}
            root.joinpath('fuel-staging-config.json').write_text(json.dumps(config))
            self.s['items']=[{'id':42,'type':'weapon_gascan','p':[60,0,4]},{'id':43,'type':'weapon_gascan','p':[300,0,284]}]
            planner=FuelPlanner(root,self.nav)
            self.assertEqual(planner.update(self.s,self.mem)['id'],42);self.assertFalse(self.mem.fuel_stage_upper)
            self.s['metrics']['pours']=3
            self.assertEqual(planner.update(self.s,self.mem)['id'],43);self.assertTrue(self.mem.fuel_stage_upper)
    def test_staging_exhaustion_returns_to_actual_lower_can(self):
        from fuel_planner import FuelPlanner
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
            self.s['metrics'].update(flags={'finale_start':True},pours=3)
            config={'enabled':True,'map':self.s['map'],'round_id':'current',
                'observer':'Astra visual review in current thread','reviewed_image':'actual.png','required_cans':8,
                'spots':[{'p':[400,0,280]}]}
            root.joinpath('fuel-staging-config.json').write_text(json.dumps(config))
            self.s['items']=[{'id':42,'type':'weapon_gascan','p':[60,0,4]}]
            planner=FuelPlanner(root,self.nav)
            self.assertEqual(planner.update(self.s,self.mem)['id'],42);self.assertFalse(self.mem.fuel_stage_upper)
    def test_distant_or_other_floor_fuel_uses_navigation_before_pickup(self):
        self.s['map']='c1m4_atrium'
        self.s['items']=[{'id':41,'type':'weapon_gascan','p':[900,0,4]},
            {'id':42,'type':'weapon_gascan','p':[60,0,144]},
            {'id':43,'type':'weapon_gascan','p':[160,0,4]}]
        _,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertNotIn('carry_41',tasks);self.assertNotIn('carry_42',tasks)
        self.assertIn('carry_43',tasks);self.assertIn('route',tasks)
    def test_fuel_occlusion_returns_to_navigation_without_false_pickup_failure(self):
        item={'id':41,'type':'weapon_gascan','p':[180,0,4]}
        self.motor._start(Task('carry_41','carry','pickup',item,25),self.s)
        self.s['t']+=13
        action=self.motor.tick(self.s,None)
        self.assertEqual(action['reason'],'target absent')
        self.assertEqual(self.mem.outcomes[-1]['status'],'interrupted')
        self.assertNotIn('carry_41',self.mem.failures)
        self.assertFalse(action['keys']);self.assertFalse(action['buttons'])
    def test_bounded_navigation_review_defers_optional_ammo_but_keeps_critical_refill(self):
        self.s['player']['weapons']['slot0'].update(type='weapon_rifle_desert',clip=32,max_clip=60,reserve=216)
        self.s['items']=[{'id':80,'type':'weapon_ammo_spawn','p':[100,0,10]}]
        self.assertIn('loot_80',packet(self.s,self.nav,self.mem)[2])
        self.mem.navigation_review=True
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertIn('route',tasks);self.assertNotIn('loot_80',tasks)
        self.s['player']['weapons']['slot0'].update(clip=26,reserve=56)
        self.assertIn('loot_80',packet(self.s,self.nav,self.mem)[2])
    def test_navigation_review_resupply_route_matches_allowed_pickup(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['round_id']='supply-review'
            ammo={'id':80,'type':'weapon_ammo_spawn','p':[100,0,10]}
            self.s['items']=[ammo];self.mem.remembered_items={80:{'item':ammo}}
            self.s['player']['weapons']['slot0'].update(type='weapon_rifle_desert',clip=26,max_clip=60,reserve=56)
            d={'map':self.s['map'],'round_id':self.s['round_id'],'version':'review','created_game_t':99,
                'expires_game_t':130,'goal_area':3,'navigation_review':True,'public_instruction':'Reach the overlook.'}
            (Path(directory)/'planner-directive.json').write_text(json.dumps(d))
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(planner.intent['id'],80)
            self.assertEqual(set(packet(self.s,self.nav,self.mem)[2]),{'loot_80'})
            self.s['player']['weapons']['slot0']['reserve']=216
            planner.update(self.s)
            self.assertIsNone(planner.intent);self.assertEqual(self.nav.goal,3)
            self.assertNotIn('loot_80',packet(self.s,self.nav,self.mem)[2])
    def test_fuel_landing_on_kiosk_roof_does_not_count_as_reviewed_ground(self):
        from fuel_planner import FuelPlanner
        for height,expected in ((143,'misplaced'),(5,'landed')):
            with self.subTest(height=height),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);self.s['map']=self.nav.map='c1m4_atrium';self.s['round_id']='current'
                self.s['metrics'].update(flags={'finale_start':True},pours=3)
                config={'enabled':True,'map':self.s['map'],'round_id':'current',
                    'observer':'Astra visual review in current thread','reviewed_image':'actual.png','required_cans':4,
                    'landing_max_z':60,'spots':[{'p':[400,0,280]}]}
                root.joinpath('fuel-staging-config.json').write_text(json.dumps(config))
                self.mem=EvidenceMemory();self.mem.released_fuel={'42':{'t':self.s['t'],'search_p':[500,0,8],'status':'released'}}
                self.s['items']=[{'id':42,'type':'weapon_gascan','p':[500,0,height]}]
                planner=FuelPlanner(root,self.nav)
                for dt in (0,.2,.6):
                    self.s['t']+=dt;planner.update(self.s,self.mem)
                self.assertEqual(planner.data['released']['42']['status'],expected)
                self.assertEqual(bool(self.mem.fuel_landing_problem),expected=='misplaced')
                if expected=='misplaced':self.assertFalse(planner.data.get('staging_complete'))
    def test_safe_nearby_primary_upgrade_does_not_offer_an_indefinite_idle(self):
        self.s['player']['inventory']['slot0']=self.s['player']['weapon']='weapon_smg'
        self.s['items']=[{'id':474,'type':'weapon_spawn','p':[140,0,10],
            'model':'models/w_models/weapons/w_rifle_ak47.mdl'}]
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertEqual(set(tasks),{'loot_474'})
        self.assertIn('w_rifle_ak47',tasks['loot_474'].description)
        self.mem.cooldowns['loot_474']=self.s['t']+10
        self.assertIn('route',packet(self.s,self.nav,self.mem)[2])
    def test_movement_and_fire_can_coexist(self):
        self.s['enemies']=[enemy()];a=self.motor.tick(self.s,choice())
        self.assertIn('w',a['keys']);self.assertIn('fire',a['buttons'])
    def test_riot_front_is_shoved_and_rear_can_be_shot(self):
        from agent_state import riot_front
        target=dict(enemy(p=[100,0,64]),model='models/infected/common_male_riot.mdl',forward=[-1,0,0])
        self.s['enemies']=[target]
        self.assertTrue(riot_front(self.s,target))
        action=self.motor.tick(self.s,choice())
        self.assertIn('shove',action['buttons']);self.assertNotIn('fire',action['buttons'])
        target['forward']=[1,0,0];self.s['t']+=1
        self.assertFalse(riot_front(self.s,target))
        action=self.motor.tick(self.s,choice())
        self.assertIn('fire',action['buttons'])
    def test_distant_frontal_riot_does_not_consume_ammo_or_stop_route(self):
        self.s['enemies']=[dict(enemy(p=[300,0,64]),model='models/infected/common_male_riot.mdl',forward=[-1,0,0])]
        action=self.motor.tick(self.s,choice())
        self.assertNotIn('fire',action['buttons']);self.assertIn('w',action['keys'])
        data,questions,_=packet(self.s,self.nav,self.mem)
        self.assertTrue(data['threats'][0]['frontal_armor'])
        self.assertIn('Frontal riot armor',questions['target']['criteria']['enemy_20'])
    def test_other_uncommon_models_are_not_assumed_bulletproof(self):
        from agent_state import riot_front
        for model in ('models/infected/common_male_ceda.mdl','models/infected/common_male_mud.mdl',''):
            target=dict(enemy(),model=model,forward=[-1,0,0])
            self.assertFalse(riot_front(self.s,target))
    def test_only_checkpoint_door_is_offered_for_safe_room_closure(self):
        self.s['exit_checkpoint_occupied']=True;self.nav.goal=1
        self.nav.areas[1].update(spawn=2048,nw=[-50,-50,0],se=[50,50,0])
        self.s['interactables']=[
            {'id':334,'type':'prop_door_rotating','door_state':2,'p':[30,0,60],'visible':True},
            {'id':405,'type':'prop_door_rotating_checkpoint','door_state':2,'p':[80,0,60],'visible':True}]
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertNotIn('use_334',tasks);self.assertIn('use_405',tasks)
    def test_nearby_occupied_safe_room_takes_priority_over_waiting_for_bot_medkit(self):
        self.s['player'].update(health=1,temp_health=21,p=[500,0,0],eye=[500,0,64])
        self.s['player']['inventory'].pop('slot3',None)
        self.s['teammates']=[{'id':2,'p':[550,0,0],'health':67,'has_medkit':True,
                             'bot':True,'dead':False,'pinned':False,'incap':False,'ledge':False}]
        self.s['exit_checkpoint_occupied']=False
        self.assertIn('await_aid',packet(self.s,self.nav,self.mem)[2])
        self.s['exit_checkpoint_occupied']=True
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertNotIn('await_aid',tasks);self.assertIn('route',tasks)
        # Measure the actual checkpoint entrance too, not just a deep corner
        # selected as the navigation goal in a long safe room.
        self.s['player'].update(p=[270,0,0],eye=[270,0,64])
        self.s['teammates'][0]['p']=[320,0,0]
        self.nav.areas[2].update(p=[650,0,0],spawn=2048,nw=[600,-40,0],se=[700,40,0])
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertNotIn('await_aid',tasks);self.assertIn('route',tasks)
    def test_close_portal_alignment_keeps_camera_facing_down_corridor(self):
        self.nav.areas[2]['p']=[0,100,0]
        self.s['player']['angles']=[5,90,0]
        for lateral in (-8,8):
            with self.subTest(lateral=lateral):
                step={'p':[lateral,0,0],'area':2,'attr':0,'link_type':'walk','align_portal':True}
                motor=SkillMotor(self.nav,self.mem)
                with patch.object(self.nav,'waypoint',return_value=step):
                    action=motor.tick(self.s,choice(target=None,combat='hold'))
                self.assertLessEqual(abs(action['dx']),1)
                self.assertIn('shift',action['keys'])
                self.assertIn('d' if lateral>0 else 'a',action['keys'])
    def test_urgent_retreat_keeps_running_and_firing_through_nav_portals(self):
        step={'p':[-200,0,0],'area':4,'attr':0,'link_type':'walk','align_portal':True}
        for kind in ('route','escape','evade'):
            with self.subTest(kind=kind):
                self.motor=SkillMotor(self.nav,self.mem)
                self.s['player']['area_damaging']=kind=='escape'
                self.s['enemies']=[enemy(kind=8 if kind=='evade' else 0,p=[200,0,64])]
                with patch.object(self.nav,'waypoint',return_value=step),patch.object(self.motor,'safe_escape',return_value=step):
                    action=self.motor.tick(self.s,choice())
                self.assertEqual(action['task'],kind)
                self.assertIn('s',action['keys']);self.assertIn('fire',action['buttons'])
                self.assertEqual('shift' in action['keys'],kind=='route')
    def test_fuel_search_route_tells_jev_that_the_can_is_unconfirmed(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            self.s['map']=self.nav.map='c1m4_atrium';self.s['metrics']['flags']={'finale_start':True}
            (Path(directory)/'fuel-search-sites.json').write_text(json.dumps({self.s['map']:[{'id':'unseen-site','p':[900,0,0]}]}))
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            data,_,tasks=packet(self.s,self.nav,self.mem)
            self.assertIn('no live can is confirmed',tasks['route'].description)
            self.assertFalse(any(task.kind=='carry' for task in tasks.values()))
            self.assertTrue(data['route']['goal']['requires_reacquisition'])
    def test_medicine_switches_to_gun_instead_of_fake_shooting(self):
        self.s['player']['weapon']='weapon_adrenaline';self.s['player']['clip']=-1;self.s['enemies']=[enemy()]
        a=self.motor.tick(self.s,choice());self.assertIn('1',a['keys']);self.assertNotIn('fire',a['buttons'])
    def test_carried_cola_not_dropped_for_ordinary_infected(self):
        self.s['player']['weapon']='weapon_cola_bottles';self.s['player']['clip']=-1;self.s['enemies']=[enemy()]
        a=self.motor.tick(self.s,choice());self.assertNotIn('1',a['keys']);self.assertNotIn('2',a['keys']);self.assertNotIn('fire',a['buttons'])
        self.assertIn('shove',a['buttons'])
    def test_carried_object_can_be_dropped_for_close_tank(self):
        self.s['player']['weapon']='weapon_cola_bottles';self.s['player']['clip']=-1;self.s['enemies']=[enemy(kind=8)]
        step={'p':[-300,0,0],'area':4,'attr':0,'link_type':'walk'}
        with patch.object(self.motor,'safe_escape',return_value=step):a=self.motor.tick(self.s,choice())
        self.assertIn('1',a['keys']);self.assertEqual(a['task'],'evade')
    def test_tank_retreat_chooses_clear_ground_away_from_the_tank(self):
        self.nav.areas[4]={'id':4,'p':[-300,0,0]};self.s['enemies']=[enemy(kind=8,p=[200,0,64])]
        self.s['nearby_nav']=[{'id':i,'damaging':False,'blocked':False} for i in (2,3,4)]
        with patch.object(self.nav,'waypoint',side_effect=lambda s,g,**kw:{'goal_area':g,'p':self.nav.areas[g]['p']}):
            step=self.motor.safe_escape(self.s,self.s['enemies'])
        self.assertEqual(step['goal_area'],4)
    def test_atrium_generic_escape_event_does_not_complete_seven_of_thirteen_cans(self):
        self.s['map']='c1m4_atrium';self.s['metrics'].update(pours=7,flags={'finale_start':True,'finale_escape_start':True})
        self.assertFalse(mission(self.s)['carry_complete']);self.assertNotEqual(mission(self.s)['phase'],'escape')
    def test_empty_gun_reloads(self):
        self.s['player']['clip']=0;self.s['enemies']=[enemy()]
        a=self.motor.tick(self.s,choice());self.assertIn('r',a['keys']);self.assertNotIn('fire',a['buttons'])
    def test_empty_pistol_reloads_despite_zero_engine_reserve(self):
        p=self.s['player'];p.update(weapon='weapon_pistol',clip=0)
        p['weapons']['slot0'].update(clip=0,reserve=0)
        p['weapons']['slot1']={'type':'weapon_pistol','clip':0,'max_clip':15,'reserve':0,'next_attack':0,'reloading':False}
        self.s['enemies']=[enemy()]
        _,questions,_=packet(self.s,self.nav,self.mem)
        self.assertIn('reload',questions['combat']['criteria'])
        action=self.motor.tick(self.s,choice())
        self.assertIn('r',action['keys']);self.assertNotIn('1',action['keys'])
    def test_resupplied_primary_is_restored_and_reloaded_before_fighting(self):
        p=self.s['player'];p.update(weapon='weapon_pistol',clip=7)
        p['inventory']['slot0']='weapon_rifle'
        p['weapons']['slot0'].update(type='weapon_rifle',clip=0,reserve=410,max_clip=50)
        p['weapons']['slot1']={'type':'weapon_pistol','clip':7,'reserve':0,'next_attack':0,'reloading':False}
        self.s['enemies']=[enemy(kind=8,p=[450,0,64])]
        facts=perceive(self.s,self.mem,self.nav.waypoint(self.s))
        self.assertFalse(facts['combat_facts']['loaded_primary_available'])
        action=self.motor.tick(self.s,choice())
        self.assertIn('1',action['keys']);self.assertNotIn('fire',action['buttons'])
        p.update(weapon='weapon_rifle',clip=0);self.s['t']+=.2
        self.assertIn('r',self.motor.tick(self.s,choice())['keys'])
        p['clip']=50;p['weapons']['slot0']['clip']=50;self.s['t']+=3
        self.assertIn('fire',self.motor.tick(self.s,choice())['buttons'])
    def test_incapacitated_player_keeps_usable_pistol_despite_loaded_primary(self):
        p=self.s['player'];p.update(weapon='weapon_pistol',clip=7,incap=True,immobilized=True)
        p['weapons']['slot1']={'type':'weapon_pistol','clip':7,'reserve':0,'next_attack':0,'reloading':False}
        self.s['enemies']=[enemy(kind=8,p=[450,0,64])]
        action=self.motor.tick(self.s,choice())
        self.assertNotIn('1',action['keys']);self.assertIn('fire',action['buttons'])
    def test_teammate_crossing_line_blocks_fire(self):
        self.s['teammates']=[bot()];self.s['enemies']=[enemy()]
        self.assertTrue(friendly_blocked(self.s,self.s['enemies'][0]['p']))
        self.assertNotIn('fire',self.motor.tick(self.s,choice())['buttons'])
    def test_calm_witch_cannot_be_selected(self):
        self.s['enemies']=[dict(enemy(kind=7),rage=0)]
        _,questions,_=packet(self.s,self.nav,self.mem);self.assertEqual(set(questions['target']['criteria']),{'none'})
    def test_hunter_reflex_between_requests(self):
        self.s['enemies']=[enemy(kind=3)];self.assertIn('shove',self.motor.tick(self.s,None)['buttons'])
    def test_charger_not_shoved(self):
        self.s['enemies']=[enemy(kind=6)];self.assertNotIn('shove',self.motor.tick(self.s,choice(combat='shove'))['buttons'])
    def test_medkit_hold_continues_and_verified_completion_ends_it(self):
        self.s['player']['weapon']='weapon_first_aid_kit';self.s['player']['health']=20
        d=choice('heal',None,'hold');self.assertIn('fire',self.motor.tick(self.s,d)['buttons'])
        s=copy.deepcopy(self.s);s['t']+=1;self.assertIn('fire',self.motor.tick(s,None)['buttons'])
        s['t']+=5;s['player']['health']=80;s['player']['inventory'].pop('slot3');s['metrics']['heals']=1
        self.assertNotIn('fire',self.motor.tick(s,d)['buttons']);self.assertEqual(self.mem.outcomes[-1]['status'],'verified')
    def test_rescue_requires_actual_recovery(self):
        b=dict(bot(),incap=True);self.s['teammates']=[b]
        d={'task':Task('rescue_2','rescue','rescue',b),'combat':'hold','target_id':None}
        self.assertIn('e',self.motor.tick(self.s,d)['keys'])
        s=copy.deepcopy(self.s);s['t']+=1;self.assertFalse(self.mem.outcomes)
        s['teammates'][0]['incap']=False;self.motor.tick(s,None);self.assertEqual(self.mem.outcomes[-1]['status'],'verified')
    def test_failed_use_cools_down(self):
        item={'id':5,'p':[70,0,64],'type':'func_button'};self.s['items']=[item]
        d={'task':Task('use_5','use','use',item,1),'combat':'hold','target_id':None}
        self.motor.tick(self.s,d);s=copy.deepcopy(self.s);s['t']+=2;self.motor.tick(s,d)
        self.assertEqual(self.mem.outcomes[-1]['status'],'failed');self.assertFalse(self.mem.allowed('use_5',s['t']))
    def test_closed_access_door_precedes_occluded_mission_button(self):
        self.s['map']='c1m2_streets'
        self.s['items']=[{'id':898,'type':'prop_door_rotating','p':[50,0,64],'door_state':0,'owned':False}]
        self.s['interactables']=[{'id':452,'type':'func_button','p':[300,0,64],'name':'gunshop_door_button','visible':False,'locked':False,'disabled':False}]
        _,questions,tasks=packet(self.s,self.nav,self.mem)
        self.assertIn('use_898',tasks);self.assertNotIn('use_452',tasks)
    def test_door_can_interrupt_attempt_to_reach_far_control(self):
        button={'id':452,'type':'func_button','p':[300,0,64]}
        door={'id':898,'type':'prop_door_rotating','p':[50,0,64],'door_state':0}
        self.s['items']=[door];self.s['interactables']=[button]
        self.motor._start(Task('use_452','use','use',button),self.s)
        decision={'task':Task('use_898','use','open',door),'combat':'hold','target_id':None}
        self.motor.tick(self.s,decision)
        self.assertEqual(self.motor.task.key,'use_898')
        self.assertEqual(self.mem.outcomes[-1]['status'],'interrupted')
    def test_collected_item_is_not_same_as_delivered(self):
        s=copy.deepcopy(self.s);s['player']['weapon']='weapon_cola_bottles'
        item={'id':5,'type':'point_prop_use_target','p':[70,0,64]};s['interactables']=[item]
        d={'task':Task('deliver_5','deliver','deliver',item),'combat':'hold','target_id':None};self.motor.tick(s,d)
        later=copy.deepcopy(s);later['t']+=3;later['player']['weapon']='weapon_pistol';self.motor.tick(later,None)
        self.assertEqual(self.mem.outcomes[-1]['status'],'needs_confirmation')
    def test_far_delivery_uses_travel_until_actual_interaction_range(self):
        self.s['player']['weapon']='weapon_cola_bottles'
        item={'id':5,'type':'point_prop_use_target','p':[500,0,64],'visible':True}
        self.s['interactables']=[item];self.s['enemies']=[enemy()]
        _,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertIn('route',tasks);self.assertNotIn('deliver_5',tasks)
        item['p']=[90,0,64];_,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertIn('deliver_5',tasks)
    def test_bot_in_exit_does_not_stop_player_outside(self):
        self.s['exit_checkpoint_occupied']=True
        self.s['player']['p']=[500,0,0]
        self.s['interactables']=[{'id':56,'type':'prop_door_rotating_checkpoint','p':[700,0,54],'door_state':2}]
        _,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertIn('route',tasks);self.assertNotIn('use_56',tasks)
        self.nav.areas[1]['spawn']=2048;self.s['player']['p']=[850,0,0];self.s['interactables'][0]['p']=[880,0,54]
        _,_,tasks=packet(self.s,self.nav,self.mem)
        self.assertNotIn('route',tasks);self.assertIn('use_56',tasks)
    def test_exit_entry_cleared_and_teammates_waited_for_before_close(self):
        self.s['exit_checkpoint_occupied']=True;self.nav.areas[1]['spawn']=2048
        self.nav.areas[1].update(nw=[800,-100,0],se=[1000,100,0])
        self.s['player']['p']=[810,0,0];self.s['teammates']=[bot(p=[750,0,0])]
        self.s['interactables']=[{'id':56,'type':'prop_door_rotating_checkpoint','p':[800,0,54],'door_state':2}]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertNotIn('use_56',tasks)
        self.s['teammates'][0]['p']=[870,0,0]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertIn('use_56',tasks)
        self.s['teammates'][0]['p']=[780,0,0]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertIn('use_56',tasks)
    def test_mall_supplies_reached_from_safe_room_not_through_wall(self):
        nav=Navigation('c1m3_mall')
        self.assertEqual(nav.closest_area([6516.8433,-1493.2284,63.556]),196057)
    def test_empty_primary_refilled_while_pistol_equipped(self):
        self.s['player']['weapon']='weapon_pistol';self.s['player']['weapons']['slot0']['reserve']=0
        self.s['items']=[{'id':6,'type':'weapon_ammo_spawn','p':[70,0,40]}]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertIn('loot_6',tasks)
    def test_dropped_upgrade_remains_pickable(self):
        self.s['items']=[{'id':6,'type':'weapon_rifle_desert','p':[70,0,40]}]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertIn('loot_6',tasks)
    def test_empty_primary_can_be_replaced_by_available_smg(self):
        self.s['player']['weapons']['slot0'].update(clip=0,reserve=0)
        self.s['items']=[{'id':6,'type':'weapon_spawn','model':'w_smg_mp5.mdl','p':[70,0,40]}]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertIn('loot_6',tasks)
    def test_only_reviewed_current_obstructions_survive_restart(self):
        base={'map':'c1m3_mall','edge':[1,2],'game_t':100,'expires_game_t':700,'confirmed_solid':True}
        self.assertEqual(active_recoveries([base],'c1m3_mall',150),{(1,2)})
        for row,t in [(dict(base,confirmed_solid=False),150),(base,50),(base,750)]:
            self.assertFalse(active_recoveries([row],'c1m3_mall',t))
    def test_briefly_occluded_supply_is_approached_but_not_used_blindly(self):
        item={'id':6,'type':'weapon_rifle_desert','p':[350,0,40]};self.s['items']=[item]
        d={'task':Task('loot_6','loot','collect rifle',item),'combat':'hold','target_id':None}
        self.motor.tick(self.s,d)
        self.s['items']=[];self.s['t']+=3
        a=self.motor.tick(self.s,d);self.assertTrue(a['keys']);self.assertNotIn('e',a['keys']);self.assertFalse(self.mem.outcomes)
        self.s['player']['p']=[300,0,0];self.s['player']['eye']=[300,0,64];self.s['t']+=1
        a=self.motor.tick(self.s,d);self.assertNotIn('e',a['keys']);self.assertFalse(self.mem.outcomes)
        self.s['t']+=9
        a=self.motor.tick(self.s,d);self.assertNotIn('e',a['keys']);self.assertEqual(self.mem.outcomes[-1]['status'],'failed')
    def test_near_visible_control_approached_directly_on_same_floor(self):
        item={'id':6,'type':'prop_door_rotating_checkpoint','p':[150,0,54],'visible':True,'door_state':2}
        self.s['interactables']=[item]
        d={'task':Task('use_6','use','close door',item),'combat':'hold','target_id':None}
        with patch.object(self.nav,'waypoint',side_effect=AssertionError('Unneeded detour')):
            a=self.motor.tick(self.s,d)
        self.assertIn('w',a['keys']);self.assertNotIn('e',a['keys'])
    def test_starting_checkpoint_door_allows_ordinary_use_attempt(self):
        self.s['items']=[{'id':6,'type':'prop_door_rotating_checkpoint','p':[70,0,54],'door_state':0,'locked':True}]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertIn('use_6',tasks)
        self.s['items'][0]['type']='prop_door_rotating'
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertNotIn('use_6',tasks)
    def test_distant_alarm_button_is_navigation_not_immediate_use(self):
        self.s['map']='c1m3_mall'
        item={'id':152,'type':'func_button','hammerid':320879,'p':[850,0,300],'visible':False}
        self.s['interactables']=[item]
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertNotIn('use_152',tasks)
        item.update(p=[70,0,64],visible=True)
        _,_,tasks=packet(self.s,self.nav,self.mem);self.assertIn('use_152',tasks)
    def test_combat_does_not_hide_blocked_travel_forever(self):
        self.motor.task=Task('route','route','travel')
        for i in range(22):
            s=copy.deepcopy(self.s);s['t']+=i;s['enemies']=[enemy()];s['metrics']['shots']=i
            self.mem.update(s)
        self.assertIn('blocked',progress_reason(s,self.mem,self.motor))
    def test_engine_delivery_event_confirms_object_consumption(self):
        self.s['player']['weapon']='weapon_cola_bottles'
        item={'id':5,'type':'point_prop_use_target','p':[70,0,64]};self.s['interactables']=[item]
        self.motor.tick(self.s,{'task':Task('deliver_5','deliver','deliver',item),'combat':'hold','target_id':None})
        later=copy.deepcopy(self.s);later['t']+=3;later['player']['weapon']='weapon_pistol'
        later['metrics']['hints']=[{'kind':'explain_store_item_stop','t':later['t']}]
        self.motor.tick(later,None)
        self.assertEqual(self.mem.outcomes[-1]['status'],'verified');self.assertTrue(mission(later)['carry_complete'])
    def test_delivery_movement_lock_does_not_release_held_button(self):
        self.s['player'].update(weapon='weapon_cola_bottles',immobilized=True)
        item={'id':5,'type':'point_prop_use_target','p':[70,0,64]};self.s['interactables']=[item]
        d={'task':Task('deliver_5','deliver','deliver',item),'combat':'hold','target_id':None}
        self.assertIn('fire',self.motor.tick(self.s,d)['buttons'])
        self.s['t']+=.2;self.assertIn('fire',self.motor.tick(self.s,d)['buttons'])
        self.s['player']['angles']=[20,-70,0]
        a=self.motor.tick(self.s,d);self.assertEqual((a['dx'],a['dy']),(0,0));self.assertIn('fire',a['buttons'])
        self.s['player']['pinned']=True
        self.assertFalse(self.motor.tick(self.s,d)['buttons'])
    def test_completed_cola_objective_not_restarted_from_old_landmark(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            root=Path(directory)
            root.joinpath('planner-directive.json').write_text(json.dumps({'map':'c1m2_streets','version':'old','created_game_t':90,'expires_game_t':190,'goal_area':2,'public_instruction':'Collect cola.'}))
            root.joinpath('mission-landmarks.json').write_text(json.dumps({'map':'c1m2_streets','game_t':90,'landmarks':{'collect cola':{'p':[200,0,0]}}}))
            self.s['metrics']['hints']=[{'kind':'explain_store_item','t':95},{'kind':'explain_store_item_stop','t':99}]
            self.s['items']=[{'id':5,'type':'prop_physics','model':'w_cola.mdl','p':[70,0,64]}]
            planner=MissionPlanner(self.nav,self.mem);planner.update(self.s)
            self.assertEqual(self.nav.goal,3);self.assertIsNone(planner.intent)
            _,_,tasks=packet(self.s,self.nav,self.mem);self.assertNotIn('carry_5',tasks)
    def test_fueled_car_escape_routes_to_car_not_finale_rooftop(self):
        from mission_planner import MissionPlanner
        with tempfile.TemporaryDirectory() as directory,patch('mission_planner.ROOT',Path(directory)):
            nav=Navigation('c1m4_atrium');old_goal=nav.goal;self.s['map']=nav.map
            self.s['metrics']['flags']={'finale_vehicle_ready':True}
            planner=MissionPlanner(nav,self.mem);planner.update(self.s)
            self.assertEqual(nav.goal,nav.closest_area([-4753.98,-3494,48]))
            self.assertNotEqual(nav.goal,old_goal);self.assertIsNone(planner.intent)
    def test_back_and_forth_cycle_detected(self):
        for i in range(29):
            s=copy.deepcopy(self.s);s['t']+=i;s['player']['p']=[(i%2)*180,0,0];self.mem.update(s)
        self.assertEqual(self.mem.movement_problem,'cycling_in_small_area')
        self.motor.task=Task('route','route','travel');self.assertTrue(progress_reason(s,self.mem,self.motor))
        self.motor.task=Task('hold','hold','defend');self.assertIsNone(progress_reason(s,self.mem,self.motor))
    def test_new_stall_not_hidden_by_earlier_travel(self):
        for i in range(21):
            s=copy.deepcopy(self.s);s['t']+=i;s['player']['p']=[min(i,9)*100,0,0];self.mem.update(s)
        self.assertEqual(self.mem.movement_problem,'standing_still')
    def test_missing_nav_only_waits_for_a_short_observed_fall(self):
        s=state();s['player'].update(area=None,velocity=[33,6,-520])
        self.assertTrue(wait_for_landing(s,self.nav,.2))
        self.assertFalse(wait_for_landing(s,self.nav,2))
        s['player']['velocity']=[0,0,0]
        self.assertFalse(wait_for_landing(s,self.nav,.2))
    def test_falling_through_isolated_known_area_waits_only_briefly(self):
        s=state();s['player']['velocity']=[-32,-558,-467]
        with patch.object(self.nav,'route',side_effect=NoRouteError('isolated upper polygon')):
            self.assertTrue(wait_for_landing(s,self.nav,.2))
            self.assertFalse(wait_for_landing(s,self.nav,2))
            s['player']['velocity']=[0,0,0]
            self.assertFalse(wait_for_landing(s,self.nav,.2))
    def test_fall_with_a_usable_route_does_not_suppress_ordinary_control(self):
        s=state();s['player']['velocity']=[40,0,-120]
        self.assertFalse(wait_for_landing(s,self.nav,.2))
    def test_falling_above_reachable_floor_releases_travel_until_landing(self):
        s=state();s['player'].update(p=[0,0,145],velocity=[40,0,-120])
        self.nav.areas[s['player']['area']].update(nw=[-20,-20,0],se=[20,20,0])
        self.assertTrue(wait_for_landing(s,self.nav,.2))
        self.assertFalse(wait_for_landing(s,self.nav,2))
        s['player']['p'][2]=20
        self.assertFalse(wait_for_landing(s,self.nav,.2))
    def test_tank_retreat_can_turn_sideways_when_wall_blocks_away_direction(self):
        s=state();s['nearby_nav']=[{'id':2,'damaging':False,'blocked':False}]
        self.nav.areas[2]['p']=[0,250,0]
        tank=enemy(kind=8,p=[-250,0,64])
        self.motor.safe_escape(s,[tank])
        self.assertEqual(self.motor.escape_goal,2)
    def test_tank_retreat_prefers_open_floor_over_short_precision_edge(self):
        s=state();s['nearby_nav']=[{'id':i,'damaging':False,'blocked':False} for i in (2,3)]
        self.nav.areas[2].update(p=[250,0,0],attr=4)
        self.nav.areas[3].update(p=[0,430,0],attr=0)
        tank=enemy(kind=8,p=[-250,0,64])
        self.motor.safe_escape(s,[tank]);self.assertEqual(self.motor.escape_goal,3)
        self.motor.escape_goal=None;s['nearby_nav']=[s['nearby_nav'][0]]
        self.motor.safe_escape(s,[tank]);self.assertEqual(self.motor.escape_goal,2)
    def test_local_tank_retreat_toward_rescue_beats_waterfront_detour(self):
        s=state();s['map']='c5m5_bridge';s['events']=[{'kind':'finale_vehicle_ready','t':90}]
        s['nearby_nav']=[{'id':i,'damaging':False,'blocked':False} for i in (1,2,3)]
        self.nav.areas[2]['p']=[-350,0,0];self.nav.areas[3]['p']=[0,350,0]
        self.nav.areas[4]={'id':4,'p':[0,900,0]};self.nav.goal=4
        self.mem.navigation_goal={'rescue':True};self.motor.escape_goal=2
        tank=enemy(kind=8,p=[250,-100,64])
        self.motor.safe_escape(s,[tank]);self.assertEqual(self.motor.escape_goal,3)
        # If the useful direction is actually blocked, retain the legal fallback.
        s['nearby_nav'][2]['blocked']=True;self.motor.escape_goal=None
        self.motor.safe_escape(s,[tank]);self.assertEqual(self.motor.escape_goal,2)
    def test_tank_retreat_bounds_short_corner_but_runs_open_approach(self):
        s=state();s['map']='c5m5_bridge';s['events']=[{'kind':'finale_vehicle_ready','t':90}]
        s['enemies']=[enemy(kind=8,p=[-100,0,64])]
        step={'p':[20,0,0],'area':2,'attr':0,'link_type':'walk','align_portal':True}
        with patch.object(self.motor,'safe_escape',return_value=step):
            action=self.motor.tick(s,choice(target=None,combat='hold'))
            self.assertIn('shift',action['keys']);self.assertEqual(action['task'],'evade')
            step['p']=[200,0,0];s['t']+=.1
            action=self.motor.tick(s,choice(target=None,combat='hold'))
            self.assertIn('w',action['keys']);self.assertNotIn('shift',action['keys'])
    def test_loaded_sidearm_finishes_close_threat_without_rifle_reload_pingpong(self):
        s=state();p=s['player'];p['weapons']['slot0'].update(clip=0,reserve=327,reloading=True)
        p['weapons']['slot1']={'type':'weapon_pistol','clip':9,'reserve':999,'max_clip':30,'next_attack':0,'reloading':False}
        s['enemies']=[dict(enemy(kind=8,p=[61,0,64]),health=81)]
        step={'p':[-200,0,0],'area':2,'attr':0,'link_type':'walk','align_portal':False}
        with patch.object(self.motor,'safe_escape',return_value=step):
            action=self.motor.tick(s,choice(target=20,combat='reload'))
            self.assertIn('2',action['keys']);self.assertNotIn('r',action['keys'])
            p.update(weapon='weapon_pistol',clip=9);s['t']+=.1
            action=self.motor.tick(s,choice(target=20,combat='fire'))
            self.assertNotIn('1',action['keys']);self.assertIn('fire',action['buttons'])
            p['weapons']['slot0'].update(clip=8,reloading=False);s['t']+=.1
            action=self.motor.tick(s,choice(target=20,combat='fire'))
            self.assertIn('1',action['keys'])
    def test_actual_rescue_route_can_be_a_tank_retreat_but_not_through_its_body(self):
        s=state();s['map']='c5m5_bridge';s['events']=[{'kind':'finale_vehicle_ready','t':90}]
        s['nearby_nav']=[{'id':i,'damaging':False,'blocked':False} for i in (1,2,3)]
        self.nav.areas[2]['p']=[-350,0,0];self.nav.areas[3]['p']=[0,350,0]
        self.mem.navigation_goal={'rescue':True}
        tank=enemy(kind=8,p=[200,0,64])
        self.motor.safe_escape(s,[tank]);self.assertEqual(self.motor.escape_goal,3)
        # A nearby goal does not permit charging into the Tank or crossing acid.
        self.motor.escape_goal=None;tank['p']=[0,350,64]
        self.motor.safe_escape(s,[tank]);self.assertEqual(self.motor.escape_goal,2)
        self.motor.escape_goal=None;tank['p']=[200,0,64];s['nearby_nav'][2]['damaging']=True
        self.motor.safe_escape(s,[tank]);self.assertEqual(self.motor.escape_goal,2)
    def test_actual_evacuation_does_not_slow_to_walk_at_every_nav_boundary(self):
        self.s['map']='c5m5_bridge';self.s['events']=[{'kind':'finale_vehicle_ready','t':90}]
        step={'p':[200,0,0],'area':2,'attr':0,'link_type':'walk','align_portal':True}
        with patch.object(self.nav,'waypoint',return_value=step):
            action=self.motor.tick(self.s,choice(target=None,combat='hold'))
        self.assertIn('w',action['keys']);self.assertNotIn('shift',action['keys'])
    def test_failed_bot_healing_wait_returns_to_play_with_bounded_cooldown(self):
        self.s['player']['health']=6;self.s['player']['inventory'].pop('slot3',None)
        teammate=bot();teammate['has_medkit']=True;self.s['teammates']=[teammate]
        task=packet(self.s,self.nav,self.mem)[2]['await_aid']
        self.motor._start(task,self.s);self.s['t']+=task.timeout
        action=self.motor.tick(self.s,{'task':task,'target_id':None,'combat':'hold'})
        self.assertNotEqual(self.motor.task.key,'await_aid')
        self.assertFalse(self.mem.allowed('await_aid',self.s['t']+119))
        self.assertTrue(self.mem.allowed('await_aid',self.s['t']+120))
        tasks=packet(self.s,self.nav,self.mem)[2]
        self.assertNotIn('await_aid',tasks);self.assertIn('route',tasks)
    def test_tank_retreat_prefers_sustained_distance_over_nearby_dead_end(self):
        nav=Navigation.__new__(Navigation)
        nav.goal=3;nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.active_ladder=None;nav.cached_start=None;nav.path=[]
        nav.areas={i:{'id':i,'p':[x,y,0],'nw':[x-10,y-10,0],'se':[x+10,y+10,0],
            'flow':i*10,'attr':0,'adj':adj}
            for i,x,y,adj in [(1,0,0,[2,3]),(2,-140,0,[]),(3,-300,150,[])]}
        self.s['nearby_nav']=[{'id':i,'blocked':False,'damaging':False} for i in nav.areas]
        tank=enemy(kind=8,p=[100,0,64])
        motor=SkillMotor(nav,self.mem);motor.safe_escape(self.s,[tank])
        self.assertEqual(motor.escape_goal,3)
        # A closed longer exit must still leave the short retreat available.
        self.s['nearby_nav'][-1]['blocked']=True;motor.escape_goal=None
        motor.safe_escape(self.s,[tank]);self.assertEqual(motor.escape_goal,2)
    def test_stale_elevator_instruction_cannot_wait_indefinitely(self):
        for i in range(29):
            s=copy.deepcopy(self.s);s['t']+=i;self.mem.update(s)
        self.motor.task=Task('hold','hold','wait for elevator')
        self.assertEqual(progress_reason(s,self.mem,self.motor),'waiting without objective progress')
        s['map']='c2m5_concert';s['metrics']['flags']={'finale_start':True}
        self.assertIsNone(progress_reason(s,self.mem,self.motor))
        s['map']='c1m2_streets';s['exit_checkpoint_occupied']=True
        self.assertIsNone(progress_reason(s,self.mem,self.motor))
    def test_fuel_delivery_routes_off_car_roof_to_standing_height(self):
        s=state();s['map']='c1m4_atrium'
        s['player'].update(p=[-4839.854,-3565.1162,93.2554],eye=[-4839.854,-3565.1162,155.2554],area=42539,weapon='weapon_gascan')
        item={'id':360,'type':'point_prop_use_target','name':'pour_target','p':[-4826,-3555,70],'visible':True}
        s['interactables']=[item];nav=Navigation(s['map']);motor=SkillMotor(nav,EvidenceMemory())
        d={'task':Task('deliver_360','deliver','pour fuel',item),'combat':'hold','target_id':None}
        action=motor.tick(s,d)
        self.assertTrue(action['keys']);self.assertNotIn('fire',action['buttons'])
        self.assertEqual(action['waypoint']['goal_area'],45162)
    def test_blocked_hazard_triggers_early_handoff(self):
        for i in range(5):
            s=copy.deepcopy(self.s);s['t']+=i;s['player']['health']-=i*5;s['player']['area_damaging']=True;self.mem.update(s)
        self.motor.task=Task('escape','escape','leave acid')
        self.assertEqual(progress_reason(s,self.mem,self.motor),'taking damage while escape movement is blocked')
    def test_stale_and_wrong_stage_api_responses_rejected(self):
        d={'map':self.s['map'],'observed_t':100,'requested_wall':10,'phase':'travel'}
        self.assertTrue(decision_fresh(d,self.s,10.5));self.assertFalse(decision_fresh(d,self.s,12))
        self.s['exit_checkpoint_occupied']=True;self.assertFalse(decision_fresh(d,self.s,10.5))
    def delayed_choice(self,task=None,target=None,combat='hold'):
        return {'task':task or Task('route','route','Continue toward fuel'),'target_id':target,'combat':combat,
            'map':self.s['map'],'round_id':self.s.get('round_id'),'observed_t':100,'requested_wall':10,'phase':'travel'}
    def test_delayed_route_uses_live_waypoint_without_renewing_its_deadline(self):
        self.s['t']=102.5;d=self.delayed_choice()
        self.assertFalse(decision_fresh(d,self.s,12.5))
        active=usable_decision(d,self.s,self.nav,self.mem,12.5)
        self.assertTrue(active['locally_revalidated'])
        self.assertEqual((active['observed_t'],active['requested_wall']),(100,10))
        self.assertIn('w',self.motor.tick(self.s,active)['keys'])
        self.s['t']=105.1
        self.assertIsNone(usable_decision(active,self.s,self.nav,self.mem,15.1))
    def test_delayed_route_cannot_override_new_hazard_or_healing_priority(self):
        self.s['t']=102;d=self.delayed_choice()
        self.s['player']['on_fire']=True
        self.assertIsNone(usable_decision(d,self.s,self.nav,self.mem,12))
        self.s['player']['on_fire']=False;self.s['player']['health']=25
        self.assertIsNone(usable_decision(d,self.s,self.nav,self.mem,12))
    def test_delayed_shot_discards_disappeared_target_and_calm_witch(self):
        self.s['t']=102;d=self.delayed_choice(target=20,combat='fire')
        for enemies in ([],[dict(enemy(20,7),rage=0)]):
            self.s['enemies']=enemies
            active=usable_decision(d,self.s,self.nav,self.mem,12)
            self.assertIsNone(active['target_id']);self.assertEqual(active['combat'],'hold')
            self.assertNotIn('fire',self.motor.tick(self.s,active)['buttons'])
    def test_delayed_combat_uses_current_target_position_and_friendly_checks(self):
        self.s['t']=102;self.s['enemies']=[enemy(20,0,[300,0,64])]
        d=self.delayed_choice(target=20,combat='fire')
        active=usable_decision(d,self.s,self.nav,self.mem,12)
        self.assertIn('fire',self.motor.tick(self.s,active)['buttons'])
        self.s['teammates']=[bot(2,[150,0,26])]
        active=usable_decision(d,self.s,self.nav,self.mem,12)
        self.assertNotIn('fire',self.motor.tick(self.s,active)['buttons'])
    def test_delayed_delivery_after_losing_fuel_is_rejected(self):
        self.s['t']=102
        item={'id':55,'type':'point_prop_use_target','p':[50,0,64]}
        self.s['interactables']=[item]
        d=self.delayed_choice(Task('deliver_55','deliver','Pour held fuel',item))
        self.assertIsNone(usable_decision(d,self.s,self.nav,self.mem,12))
    def test_delayed_decision_cannot_cross_round_or_phase(self):
        self.s['t']=102;d=self.delayed_choice()
        self.s['round_id']='new-round'
        self.assertIsNone(usable_decision(d,self.s,self.nav,self.mem,12))
        self.s.pop('round_id');self.s['exit_checkpoint_occupied']=True
        self.assertIsNone(usable_decision(d,self.s,self.nav,self.mem,12))
    def test_personal_fields_not_sent_to_jev(self):
        self.s['account']='SECRET_ACCOUNT';self.s['player']['steamid']='SECRET_ID';self.s['desktop_title']='PRIVATE_WINDOW'
        data,_,_=packet(self.s,self.nav,self.mem);rendered=json.dumps(data)
        for secret in ('SECRET_ACCOUNT','SECRET_ID','PRIVATE_WINDOW'):self.assertNotIn(secret,rendered)
    def test_visual_expiry_pose_and_unexpected_text(self):
        facts={'gameplay_only':True,'menu_or_console':False,'uncertain':False,'route_hint':'none','objects':[{'kind':'ladder','region':'left','state':'climbable'}]}
        report={'capture':{'map':self.s['map'],'game_t':100,'captured_unix':200,'position':[0,0,0],'angles':[0,0,0]},'facts':facts}
        self.assertIsNotNone(accepted_facts(report,self.s,201));self.assertIsNone(accepted_facts(report,self.s,260))
        self.s['player']['p']=[181,0,0];self.assertIsNone(accepted_facts(report,self.s,201))
        facts['account_name']='private';self.assertRaises(ValueError,validate_facts,facts)
    def test_mission_knowledge_all_original_chapters(self):self.assertEqual(len(CHAPTERS),23)
    def test_delivery_landmark_survives_restart_and_overrides_collection_route(self):
        from mission_planner import MissionPlanner
        target={'id':368,'type':'point_prop_use_target','p':[-5372,-1980,669],'name':'cola_delivered'}
        with patch('mission_planner.ROOT',Path(tempfile.mkdtemp())) as root:
            (root/'mission-landmarks.json').write_text(json.dumps({'map':'c1m2_streets','game_t':90,'landmarks':{'deliver cola':target}}))
            (root/'planner-directive.json').write_text(json.dumps({'version':'collect','map':'c1m2_streets','created_game_t':90,'expires_game_t':200,'goal_area':415979,'public_instruction':'Collect cola'}))
            nav=Navigation('c1m2_streets');planner=MissionPlanner(nav,EvidenceMemory());s=state()
            s['map']='c1m2_streets';s['player']['weapon']='weapon_cola_bottles';planner.update(s)
            self.assertEqual(nav.goal,nav.closest_area(target['p']));self.assertNotEqual(nav.goal,415979)
    def test_restart_discards_landmarks_from_later_attempt(self):
        from mission_planner import MissionPlanner
        with patch('mission_planner.ROOT',Path(tempfile.mkdtemp())) as root:
            (root/'mission-landmarks.json').write_text(json.dumps({'map':'c1m2_streets','game_t':150,'landmarks':{'deliver cola':{'p':[-5372,-1980,669]}}}))
            nav=Navigation('c1m2_streets');planner=MissionPlanner(nav,EvidenceMemory());s=state();s['map']='c1m2_streets'
            planner.update(s);self.assertFalse(planner.landmarks)
    def test_world_heading_independent_from_aim(self):
        self.assertEqual(movement_keys([0,0,0],[100,0,0],90),['d'])
        self.assertEqual(movement_keys([0,0,0],[100,0,0],180),['s'])
    def test_actual_ladder_landing_below_endpoint(self):
        nav=Navigation('c1m2_streets');ladder=next(l for l in nav.ladders if l['id']==66)
        nav.active_ladder=(ladder,True);s=copy.deepcopy(self.s)
        s['player'].update(p=[-2365,2437.5,87.3013],area=ladder['top_area'],flow=0)
        self.assertIsNone(nav.ladder_waypoint(s));self.assertIsNone(nav.active_ladder)
        self.assertIsInstance(nav.waypoint(s),dict)
    def test_goal_uses_exit_checkpoint_instead_of_optional_dead_end(self):
        nav=Navigation('c1m2_streets');self.assertEqual(nav.goal,232687)
        self.assertTrue(nav.areas[nav.goal]['spawn']&2048)
    def test_observed_fence_rooftop_not_used_as_survivor_shortcut(self):
        nav=Navigation('c1m2_streets');route=nav.route(191231)
        self.assertNotIn(393221,route)
        self.assertTrue(all(0<=nav.areas[i]['flow']<1e7 for i in route[1:-1]))
    def test_gun_shop_route_uses_entrance_not_floor_above(self):
        nav=Navigation('c1m2_streets');route=nav.route(403650)
        self.assertIn(201807,route[:5])
        for u,v in zip(route,route[1:]):
            if (u,v) in nav.ladder_edges:continue
            a,b=nav.areas[u],nav.areas[v]
            self.assertLessEqual(min(b['nw'][2],b['se'][2])-max(a['nw'][2],a['se'][2]),65)
    def test_observed_balcony_corner_aligns_before_crossing(self):
        nav=Navigation('c1m2_streets');s=state()
        s['player'].update(p=[-5279.9688,-2101.2837,616.0313],area=206688)
        step=nav.waypoint(s,201297)
        self.assertTrue(step['align_portal']);self.assertEqual(step['p'][1],s['player']['p'][1])
        self.assertLess(step['p'][0],s['player']['p'][0])
        s['player']['p'][0]=step['p'][0];step=nav.waypoint(s,201297)
        self.assertTrue(step['align_portal']);self.assertEqual(step['p'][0],s['player']['p'][0])
        self.assertLess(step['p'][1],-2116)
        s['player']['p'][1]=-2120;step=nav.waypoint(s,201297)
        self.assertFalse(step['align_portal']);self.assertLess(step['p'][0],-5325)
        self.assertTrue(-2124<=step['p'][1]<=-2116)
    def test_exact_area_counter_corner_steps_inward_before_lateral_alignment(self):
        nav=Navigation('c1m4_atrium');s=state();s['map']=nav.map
        s['player'].update(p=[-4206.2036,-3855.9688,.0313],area=8499,nearest_area=False)
        step=nav.waypoint(s,25280)
        self.assertTrue(step['align_portal']);self.assertEqual(step['p'][0],s['player']['p'][0])
        self.assertLessEqual(step['p'][1],-3874)
        s['player']['p'][1]=-3874;step=nav.waypoint(s,25280)
        self.assertEqual(step['p'][1],-3874);self.assertLess(step['p'][0],-4218)
    def test_escape_reaches_observed_clear_floor_beyond_small_radius(self):
        nav=Navigation('c1m2_streets');s=state()
        s['player'].update(p=[-3867.5696,1847.5198,320.0313],area=398620,area_damaging=True)
        s['nearby_nav']=[{'id':393594,'damaging':False,'blocked':False}]
        motor=SkillMotor(nav,EvidenceMemory());waypoint=motor.safe_escape(s)
        self.assertEqual(waypoint['goal_area'],393594)
        self.assertLessEqual(waypoint['remaining_areas'],8)
    def test_escape_can_finish_stair_slope_without_false_jump_rejection(self):
        nav=Navigation('c1m4_atrium');s=state();s['map']=nav.map
        s['player'].update(p=[-3772.1909,-4015.8284,144.6655],area=3098,area_damaging=True)
        s['nearby_nav']=[{'id':2697,'damaging':False,'blocked':False}]
        waypoint=SkillMotor(nav,EvidenceMemory()).safe_escape(s)
        self.assertEqual(waypoint['goal_area'],2697);self.assertEqual(waypoint['remaining_areas'],3)
    def test_escape_can_walk_up_remaining_flight_to_clear_stair_area(self):
        nav=Navigation('c1m4_atrium');s=state();s['map']=nav.map
        s['player'].update(p=[-3756.9521,-4159.8472,54.1472],area=3097,area_damaging=True)
        s['nearby_nav']=[{'id':3098,'damaging':False,'blocked':False}]
        waypoint=SkillMotor(nav,EvidenceMemory()).safe_escape(s)
        self.assertEqual(waypoint['goal_area'],3098)
    def test_escape_route_rejects_large_drop_even_to_clear_floor(self):
        nav=Navigation.__new__(Navigation);nav.goal=2;nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.areas={i:{'id':i,'p':[i*30,0,z],'nw':[i*30-15,-10,z],'se':[i*30+15,10,z],'flow':i*30,'adj':[2] if i==1 else []} for i,z in [(1,200),(2,0)]}
        self.assertRaises(RuntimeError,nav.route,1,2,escaping=True)
    def test_reviewed_one_way_drop_is_scoped_and_never_a_tank_escape_shortcut(self):
        nav=Navigation.__new__(Navigation);nav.goal=2;nav.avoided=set();nav.blocked=set();nav.damaging=set();nav.ladder_edges={}
        nav.areas={i:{'id':i,'p':[i*30,0,z],'nw':[i*30-15,-10,z],'se':[i*30+15,10,z],'flow':i*30,'adj':[2] if i==1 else []} for i,z in [(1,199),(2,0)]}
        self.assertRaises(NoRouteError,nav.route,1,2)
        self.s['round_id']='reviewed-round'
        review={'map':self.s['map'],'round_id':'reviewed-round','created_game_t':99,'expires_game_t':200,
            'drop_edges':[{'edge':[1,2],'max_drop':210}],'blocked_edges':[]}
        _,nav.reviewed_drops=reviewed_links(review,self.s,nav.areas)
        self.assertEqual(nav.route(1,2),[1,2]);self.assertRaises(NoRouteError,nav.route,1,2,escaping=True)
        for altered in [dict(review,round_id='old'),dict(review,expires_game_t=99),dict(review,map='another'),
                        dict(review,drop_edges=[{'edge':[1,2],'max_drop':500}])]:
            self.assertEqual(reviewed_links(altered,self.s,nav.areas)[1],{})
    def test_atrium_fuel_return_uses_stairs_instead_of_damaging_balcony_drop(self):
        nav=Navigation('c1m4_atrium');route=nav.route(1207,45162)
        self.assertGreater(len(route),2)
        for u,v in zip(route,route[1:]):
            if (u,v) in nav.ladder_edges:continue
            a,b=nav.areas[u],nav.areas[v]
            drop=min(a['nw'][2],a['se'][2])-max(b['nw'][2],b['se'][2])
            self.assertLessEqual(drop,120)
    def test_observed_stair_lip_does_not_prevent_jump(self):
        self.s['player'].update(p=[-5269.8975,895.4684,416.0313],eye=[-5269.8975,895.4684,480.0313],angles=[5,-.3305,0],velocity=[0,0,0])
        waypoint={'p':[-5155.201,894.799,464.0313],'attr':0,'link_type':'walk'}
        with patch.object(self.nav,'waypoint',return_value=waypoint):
            action=self.motor.tick(self.s,choice('route',None,'hold'))
        self.assertIn('space',action['keys'])
    def test_atrium_continuous_stairs_do_not_jump_onto_center_railing(self):
        nav=Navigation('c1m4_atrium');nav.goal=82;s=state();s['map']=nav.map
        s['player'].update(p=[-3772.2192,-4070.3,110.4],eye=[-3772.2192,-4070.3,172.4],area=3098,angles=[5,90,0],velocity=[0,0,0])
        action=SkillMotor(nav,EvidenceMemory()).tick(s,choice('route',None,'hold'))
        self.assertGreater(action['waypoint']['p'][2]-s['player']['p'][2],18)
        self.assertIn('w',action['keys']);self.assertNotIn('space',action['keys'])
    def test_ladder_top_still_needs_dismount(self):
        nav=Navigation('c1m2_streets');ladder=next(l for l in nav.ladders if l['id']==66)
        nav.active_ladder=(ladder,True);s=copy.deepcopy(self.s)
        s['player'].update(p=list(ladder['top']),area=ladder['top_area'],on_ladder=True,flow=0)
        self.assertEqual(nav.ladder_waypoint(s)['phase'],'dismount')
    def test_all_regression_scenarios_build_legal_questions(self):
        for name,s,expect in scenarios():
            with self.subTest(name=name):
                _,questions,tasks=packet(s,FlatNav(),EvidenceMemory())
                for key,allowed in expect.items():self.assertTrue(allowed&questions[key]['criteria'].keys())
    def test_continuous_use_has_no_release_between_ticks(self):
        import game_input
        control=game_input.GameInput.__new__(game_input.GameInput)
        control.pid=123;control.held_keys=set();control.held_buttons=set();control.guard=lambda:None
        sent=[]
        with patch.object(game_input,'send',side_effect=sent.append),patch.object(game_input,'foreground_pid',return_value=123):
            control.sustain(keys=['e'],seconds=.05);control.sustain(keys=['e'],seconds=.05)
            self.assertEqual(len(sent),1)
            control.sustain(seconds=.05);self.assertEqual(len(sent),2)
            self.assertTrue(sent[-1].ki.flags&2)
    def test_bounded_mouse_correction_precedes_new_trigger_press(self):
        import game_input
        control=game_input.GameInput.__new__(game_input.GameInput)
        control.pid=123;control.held_keys=set();control.held_buttons=set();control.guard=lambda:None
        sent=[]
        with patch.object(game_input,'send',side_effect=sent.append),patch.object(game_input,'foreground_pid',return_value=123):
            control.sustain(dx=100,dy=-20,buttons=['fire'],seconds=.05)
        self.assertEqual(len(sent),2)
        self.assertEqual(sent[0].mi.dwFlags,1);self.assertEqual(sent[0].mi.dx,100)
        self.assertEqual(sent[1].mi.dwFlags,game_input.BUTTONS['fire'][0])
    def test_capture_guard_failure_releases_held_controls(self):
        import game_input
        control=game_input.GameInput.__new__(game_input.GameInput);control.pid=123
        control.held_keys={'w'};control.held_buttons={'fire'}
        def failure():raise RuntimeError('Capture unavailable')
        control.guard=failure
        with patch.object(game_input,'send'),patch.object(game_input,'foreground_pid',return_value=123):
            self.assertRaises(RuntimeError,control.sustain,keys=['w'],buttons=['fire'])
        self.assertEqual(control.held_keys,set());self.assertEqual(control.held_buttons,set())
    def test_focus_loss_releases_only_controller_owned_inputs_without_new_presses(self):
        import game_input
        control=game_input.GameInput.__new__(game_input.GameInput);control.pid=123
        control.held_keys={'w'};control.held_buttons={'fire'}
        def failure():raise RuntimeError('Game lost foreground ownership')
        control.guard=failure;sent=[]
        with patch.object(game_input,'send',side_effect=sent.append),patch.object(game_input,'foreground_pid',return_value=456):
            self.assertRaises(RuntimeError,control.sustain,keys=['d'],buttons=['shove'])
        self.assertEqual(len(sent),2);self.assertTrue(sent[0].ki.flags&2)
        self.assertEqual(sent[1].mi.dwFlags,game_input.BUTTONS['fire'][1])
        self.assertEqual(control.held_keys,set());self.assertEqual(control.held_buttons,set())
    def test_paid_vision_refuses_unconfigured_budget(self):
        from vision_observer import paid_analysis
        with patch('vision_observer.ROOT',Path(tempfile.mkdtemp())) as root:
            (root/'vision-config.json').write_text('{"enabled":false,"budget_usd":0}')
            self.assertRaises(RuntimeError,paid_analysis,{})
    def test_failed_combat_is_observed_not_assumed_successful(self):
        monitor=CombatMonitor();s=state();s['enemies']=[enemy()]
        action={'target_id':20,'buttons':['fire']};self.assertIsNone(monitor.update(s,action))
        s['t']+=9;s['metrics']['shots']=9;self.assertIsNotNone(monitor.update(s,action))
        s['enemies'][0]['health']=20;self.assertIsNone(monitor.update(s,action))

class WitchRouteTests(unittest.TestCase):
    def setUp(self):
        self.nav=Navigation.__new__(Navigation);n=self.nav
        n.goal=3;n.avoided=set();n.blocked=set();n.damaging=set();n.ladder_edges={};n.cached_start=1
        n.areas={i:{'id':i,'p':[x,y,0],'nw':[x-20,y-20,0],'se':[x+20,y+20,0],
            'flow':i*10,'attr':0,'adj':adj} for i,x,y,adj in [(1,0,0,[2,4]),(2,400,0,[3]),
                (3,800,0,[]),(4,0,350,[5]),(5,800,350,[3])]}
        self.s=state();self.s['enemies']=[dict(enemy(kind=7,p=[400,0,42]),rage=0)]
    def test_calm_witch_prefers_longer_walkable_route(self):
        self.nav.update(self.s)
        self.assertEqual(self.nav.route(1),[1,4,5,3]);self.assertIsNone(self.nav.cached_start)
    def test_missing_angry_and_other_floor_witch_do_not_keep_detour(self):
        self.nav.update(self.s)
        for enemies in [[],[dict(enemy(kind=7,p=[400,0,42]),rage=1)],
                        [dict(enemy(kind=7,p=[400,0,500]),rage=0)],
                        [dict(enemy(kind=7,p=[400,0,42]),rage=0,health=0)]]:
            self.s['enemies']=enemies;self.nav.update(self.s)
            self.assertEqual(self.nav.route(1),[1,2,3])
    def test_witch_avoidance_does_not_authorize_unwalkable_height(self):
        self.nav.areas[4].update(p=[0,350,200],nw=[-20,330,200],se=[20,370,200])
        self.nav.update(self.s)
        self.assertEqual(self.nav.route(1),[1,2,3])

class LocalMotionTests(unittest.TestCase):
    def setUp(self):
        self.s=state();self.nav=FlatNav();self.motion=MotionRecovery(movement_keys)
        self.nav.areas[1].update(nw=[-200,-200,0],se=[250,200,0])
        self.s['nearby_nav']=[{'id':1,'blocked':False,'damaging':False}]
        self.s['obstacles']=[{'angle':a,'fraction':.1 if a==0 else 1} for a in range(-150,181,30)]
        self.action={'task':'route','keys':['w','shift'],'buttons':['fire'],'dx':0,'dy':0,'waypoint':{'p':[200,0,0]}}
    def stalled(self,seconds=1.2):
        result=None
        for _ in range(round(seconds/.1)):
            self.s['t']+=.1;result=self.motion.adjust(self.s,dict(self.action),self.nav)
        return result
    def test_calm_witch_gets_supported_sidestep_without_canceling_combat(self):
        self.s['enemies']=[dict(enemy(kind=7,p=[180,0,42]),rage=0)]
        result=self.motion.avoid_calm_witches(self.s,self.action,self.nav)
        self.assertIn('witch_avoidance',result);self.assertNotIn('w',result['keys'])
        self.assertTrue(set(result['keys'])&{'a','s','d'});self.assertEqual(result['buttons'],['fire'])
        self.assertIsNone(self.motion.blocked_reason)
    def test_no_supported_sidestep_stops_before_witch_for_review(self):
        self.s['enemies']=[dict(enemy(kind=7,p=[180,0,42]),rage=0)]
        self.s['nearby_nav'][0]['blocked']=True
        result=self.motion.avoid_calm_witches(self.s,self.action,self.nav)
        self.assertFalse(set(result['keys'])&{'w','a','s','d'})
        self.assertIn('Calm Witch',self.motion.blocked_reason)
    def test_movement_away_angry_or_different_floor_is_not_blocked(self):
        self.s['enemies']=[dict(enemy(kind=7,p=[180,0,42]),rage=0)]
        away=dict(self.action,keys=['s'])
        self.assertEqual(self.motion.avoid_calm_witches(self.s,away,self.nav),away)
        for e in [dict(enemy(kind=7,p=[180,0,42]),rage=1),dict(enemy(kind=7,p=[180,0,400]),rage=0)]:
            self.s['enemies']=[e]
            self.assertEqual(self.motion.avoid_calm_witches(self.s,self.action,self.nav),self.action)
    def test_blocked_furniture_uses_clear_floor_sidestep_and_keeps_combat(self):
        result=self.stalled()
        self.assertIn('local_detour',result)
        self.assertNotEqual(result['keys'],self.action['keys']);self.assertNotIn('shift',result['keys'])
        self.assertEqual(result['buttons'],['fire'])
    def test_retreat_reacts_to_observed_vehicle_without_waiting_for_a_stall(self):
        self.action['task']='evade'
        for r in self.s['obstacles']:
            if r['angle']==0:r['hit_type']='prop_physics'
        result=self.motion.adjust(self.s,self.action,self.nav)
        self.assertIn('local_detour',result);self.assertEqual(result['buttons'],['fire'])
        self.assertIsNone(self.motion.blocked_reason)
    def test_retreat_blocked_by_vehicle_and_drop_requests_route_review_immediately(self):
        self.action['task']='evade'
        for r in self.s['obstacles']:
            if r['angle']==0:r['hit_type']='prop_physics'
        self.nav.areas[1].update(nw=[-10,-10,0],se=[10,10,0])
        result=self.motion.adjust(self.s,self.action,self.nav)
        self.assertNotIn('local_detour',result)
        self.assertIn('Observed solid obstruction',self.motion.blocked_reason)
    def test_clear_ray_is_rejected_when_its_floor_has_a_gap_or_is_blocked(self):
        self.nav.areas[1]['nw']=[-10,-10,0];self.nav.areas[1]['se']=[10,10,0]
        self.assertNotIn('local_detour',self.stalled())
        self.nav.areas[1].update(nw=[-200,-200,0],se=[250,200,0]);self.motion.reset()
        self.s['nearby_nav'][0]['blocked']=True
        self.assertNotIn('local_detour',self.stalled())
    def test_all_blocked_rays_do_not_invent_a_detour(self):
        for ray in self.s['obstacles']:ray['fraction']=.05
        self.assertNotIn('local_detour',self.stalled())
    def test_arrived_waypoint_does_not_report_blocked_movement(self):
        self.s['obstacles']=[]
        for _ in range(61):
            self.s['t']+=.1;self.motion.adjust(self.s,self.action,self.nav)
        self.s['t']+=.1
        self.motion.adjust(self.s,dict(self.action,keys=[]),self.nav)
        self.assertIsNone(self.motion.blocked_reason)
    def test_tactical_switches_do_not_erase_actual_blocked_movement(self):
        self.s['obstacles']=[]
        for i in range(36):
            self.s['t']+=.1;a=dict(self.action,task='evade' if i%3==0 else 'route')
            self.motion.adjust(self.s,a,self.nav)
        self.assertIn('across tactical',self.motion.blocked_reason)
    def test_fence_oscillation_is_bounded_despite_small_movements_and_task_switches(self):
        self.s['obstacles']=[]
        for i in range(320):
            self.s['t']+=.1;self.s['player']['p']=[40*math.sin(i/8),30*math.cos(i/8),0]
            action=dict(self.action,task='hold' if i%4==0 else 'route')
            self.motion.adjust(self.s,action,self.nav)
        self.assertIn('cycling around the same obstruction',self.motion.blocked_reason)
    def test_long_travel_or_vertical_progress_is_not_a_fence_loop(self):
        self.s['obstacles']=[]
        for vertical in (False,True):
            self.motion.reset()
            for i in range(320):
                self.s['t']+=.1;self.s['player']['p']=[0 if vertical else i*2,0,i if vertical else 0]
                self.motion.adjust(self.s,self.action,self.nav)
            self.assertIsNone(self.motion.blocked_reason)
    def test_deliberate_evasion_is_not_travel_oscillation(self):
        self.s['obstacles']=[]
        for i in range(320):
            self.s['t']+=.1;self.s['player']['p']=[40*math.sin(i/8),30*math.cos(i/8),0]
            self.motion.adjust(self.s,dict(self.action,task='evade'),self.nav)
        self.assertIsNone(self.motion.blocked_reason)
    def test_healing_and_incapacitation_do_not_become_movement_failures(self):
        self.stalled();self.s['player']['incap']=True
        result=self.motion.adjust(self.s,self.action,self.nav)
        self.assertNotIn('local_detour',result);self.assertFalse(self.motion.history)
        self.s['player']['incap']=False
        self.motion.adjust(self.s,dict(self.action,task='heal',keys=[]),self.nav)
        self.assertFalse(self.motion.history)
    def test_sidestep_does_not_move_closer_to_a_tank(self):
        self.s['enemies']=[enemy(kind=8,p=[80,0,64])]
        result=self.stalled()
        self.assertIn('local_detour',result)
        point=result['local_detour']['p']
        self.assertGreaterEqual(((point[0]-80)**2+point[1]**2)**.5,75)

class ConcertArrivalTests(unittest.TestCase):
    def test_arrival_defends_until_collision_delay_without_asserting_win(self):
        s=state();s['map']='c2m5_concert'
        s['metrics']['flags']={'finale_start':True,'finale_escape_start':True}
        s['metrics']['recent']=[{'kind':'finale_escape_start','t':100}]
        for elapsed,expected in [(0,'defend'),(1,'defend'),(17.9,'defend'),(18,'escape'),(60,'escape')]:
            s['t']=100+elapsed
            self.assertEqual(mission(s)['phase'],expected)
            self.assertEqual(s['outcome'],'active')
        s['metrics']['recent']=[]
        self.assertEqual(mission(s)['phase'],'escape')

class RescueUrgencyTests(unittest.TestCase):
    def test_ready_rescue_does_not_repeat_unconfirmed_bot_healing_wait(self):
        s=state();s['player']['health']=11;s['player']['inventory'].pop('slot3')
        s['teammates']=[dict(bot(),has_medkit=True)]
        self.assertIn('await_aid',packet(s,FlatNav(),EvidenceMemory())[2])
        s['map']='c2m5_concert';s['metrics']['flags']={'finale_vehicle_ready':True}
        tasks=packet(s,FlatNav(),EvidenceMemory())[2]
        self.assertNotIn('await_aid',tasks);self.assertIn('route',tasks)

class ShotgunResupplyTests(unittest.TestCase):
    def test_full_shotgun_is_not_below_rifle_minimum(self):
        from agent_state import needs_ammo_resupply,critical_ammo_resupply
        s=state();p=s['player'];gun=p['weapons']['slot0']
        for kind,cap,reserve in [('weapon_pumpshotgun',8,56),('weapon_shotgun_chrome',8,72),('weapon_autoshotgun',10,90)]:
            gun.update(type=kind,max_clip=cap,clip=cap,reserve=reserve)
            self.assertFalse(needs_ammo_resupply(p));self.assertFalse(critical_ammo_resupply(p))
            gun.update(clip=0,reserve=5)
            self.assertTrue(needs_ammo_resupply(p));self.assertTrue(critical_ammo_resupply(p))

if __name__=='__main__':unittest.main(verbosity=2)
