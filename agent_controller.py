"""Asynchronous Jev tactics with a fast, feedback-driven game motor.

One decision request at a time. Stale decisions never become long input holds.
Planner/vision can supply public facts asynchronously without controlling keys.
"""
import argparse
import concurrent.futures
import datetime
import json
import time
import traceback
from dataclasses import asdict
from obs_guard import ROOT,finalize_recording
from observe_game import EMS,read_json,observe
from game_input import GameInput,foreground_pid,game_process_exited
from navigation import Navigation,distance,flat_distance,segment_distance,NoRouteError
from jev_bridge import JevClient,commentary,TransientJevError
from agent_state import EvidenceMemory
from agent_policy import packet,compose,Task
from agent_motor import SkillMotor,movement_keys
from local_motion import MotionRecovery
from mission_planner import MissionPlanner
from game_knowledge import mission

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def completed_request(pending,consecutive_timeouts):
    """Permit two temporary failures; never resend an old decision payload."""
    try:return pending.result(),0
    except (TimeoutError,TransientJevError):
        if consecutive_timeouts>=2:raise
        return None,consecutive_timeouts+1

def log(name,row):
    with (ROOT/name).open('a',encoding='utf-8') as f:f.write(json.dumps(row,default=str)+'\n')
def snapshot(name,row):
    path=ROOT/name;tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(row,indent=2,default=str),encoding='utf-8')
    for attempt in range(5):
        try:tmp.replace(path);return
        except PermissionError:
            if attempt==4:raise
            time.sleep(.02*(attempt+1))

def decision_fresh(decision,state,now,max_age=1.6):
    return bool(decision and decision['map']==state['map']
        and decision.get('round_id')==state.get('round_id')
        and 0<=state['t']-decision['observed_t']<=max_age
        and 0<=now-decision['requested_wall']<=max_age
        and decision['phase']==mission(state)['phase'])

def usable_decision(decision,state,nav,memory,now):
    """Recheck a delayed high-level choice against current legal actions.

    Network replies can take longer than the short combat freshness window.
    Rebuild the selected task and validate its target/combat from live state;
    never extend the original timestamps or replay old mouse/key commands.
    """
    if decision_fresh(decision,state,now):return decision
    if not decision_fresh(decision,state,now,max_age=5):return None
    _,questions,tasks=packet(state,nav,memory)
    task=tasks.get(decision['task'].key)
    if task is None or task.kind!=decision['task'].kind:return None
    target=decision['target_id'];combat=decision['combat']
    if target is not None and 'enemy_'+str(target) not in questions['target']['criteria']:
        target=None
    if combat not in questions['combat']['criteria'] or (target is None and combat in ('fire','shove')):
        combat='hold'
    return dict(decision,task=task,target_id=target,combat=combat,locally_revalidated=True)

def wait_for_landing(s,nav,elapsed):
    p=s['player']
    if not (p.get('velocity',[0,0,0])[2]<-30 and elapsed<2
        and not (p['dead'] or p['pinned'] or p.get('on_ladder'))):return False
    if p['area'] not in nav.areas:return True
    floor=nav.areas[p['area']]
    if ('nw' in floor and 'se' in floor
        and p['p'][2]-max(floor['nw'][2],floor['se'][2])>60):
        # A falling survivor can already be assigned the reachable floor
        # below. Steering toward its next portal while still high above it
        # can carry us beyond that landing and over the bridge edge.
        return True
    # A downhill fall can briefly select an isolated upper polygon even
    # though the landing below has a normal route. Let the observed fall
    # finish instead of inventing a graph connection or replaying inputs.
    try:nav.route(p['area'],nav.goal)
    except NoRouteError:return True
    return False

def wait_for_elevator(s,elapsed):
    """A live platform can carry us between the static navigation floors."""
    p=s['player']
    limit=60 if s['map']=='c4m3_sugarmill_b' else 45
    if elapsed>=limit or p['dead'] or p['pinned'] or p.get('on_ladder'):return False
    return any(e['type']=='func_elevator' and flat_distance(p['p'],e['p'])<110
        and -8<=p['p'][2]-e['p'][2]<=20 for e in s.get('interactables',[]))

def wait_for_pinned_rescue(s,elapsed):
    """A Charger carry is forced motion, not an invalid walking route."""
    p=s['player']
    return (p['pinned'] and not p['dead'] and elapsed<15 and
        any(not (b['dead'] or b['incap'] or b['pinned'] or b.get('ledge'))
            and distance(p['p'],b['p'])<1000 for b in s['teammates']))

def known_ladder_transit(s,nav):
    """A tracked physical ladder does not require a floor under the player."""
    p=s['player'];active=getattr(nav,'active_ladder',None)
    if not active or not p.get('on_ladder') or p['dead'] or p['pinned'] or p['incap']:return False
    ladder,_=active
    return segment_distance(p['p'],ladder['bottom'],ladder['top'])<=48

def wait_for_ledge_rescue(s,elapsed):
    p=s['player']
    return bool(p.get('ledge') and not p['dead'] and elapsed<45
        and any(not (b['dead'] or b['incap'] or b['pinned'] or b.get('ledge'))
            and distance(p['p'],b['p'])<1000 for b in s['teammates']))

def progress_reason(s,memory,motor):
    p=s['player'];task=motor.task
    if not task or task.kind not in ('route','escape','evade','hold'):return None
    if p['pinned'] or p['incap'] or p.get('immobilized'):return None
    if wait_for_elevator(s,0) and any(0<s['t']-h[0]<2 and abs(p['p'][2]-h[1][2])>2 for h in memory.history):
        # Real vertical transport is progress. Discard the earlier button
        # approach stall, but keep stationary elevator waits bounded normally.
        memory.reset_motion();return None
    if task.kind=='hold':
        if task.key=='await_aid' and s['t']-motor.started>=task.timeout:
            return 'Nearby teammate did not complete healing during the bounded aid wait'
        # A stale planner instruction must not turn a short wait into an
        # indefinite idle loop. Actual holdout finales and checkpoint waits
        # remain valid; review prolonged stationary waiting elsewhere.
        if mission(s)['phase'] in ('defend','safe_room'):return None
        waiting=[h for h in memory.history if s['t']-h[0]<=30 and h[0]>=motor.started]
        if waiting and s['t']-waiting[0][0]>=25 and max(distance(p['p'],h[1]) for h in waiting)<45:
            return 'waiting without objective progress'
        return None
    recent=[h for h in memory.history if s['t']-h[0]<=4 and h[0]>=motor.started]
    if task.kind=='evade' and recent and s['t']-recent[0][0]>3 and max(distance(p['p'],h[1]) for h in recent)<45:
        tanks=[e for e in s['enemies'] if e['type']==8]
        # A completed retreat can end on a raised walkway with no useful
        # further escape. Briefly retain demonstrably effective defense at
        # range, while every motor tick still looks for an ordinary route.
        # Shots alone, unseen targets or proximity never waive this check.
        productive_defense=(motor.escape_goal is None and tanks
            and all(distance(p['eye'],e['p'])>550 for e in tanks)
            and memory.recent_damage<5 and memory.recent_shots>0
            and memory.recent_tank_damage>0 and s['t']-motor.started<12)
        if not productive_defense:return 'Tank retreat is blocked'
    if p['area_damaging'] and recent and s['t']-recent[0][0]>3 and max(distance(p['p'],h[1]) for h in recent)<45 and memory.recent_damage>=10:
        return 'taking damage while escape movement is blocked'
    longer=[h for h in memory.history if s['t']-h[0]<=22 and h[0]>=motor.started]
    if longer and s['t']-longer[0][0]>=20 and max(distance(p['p'],h[1]) for h in longer)<60:
        return 'travel remains blocked even while combat continues'
    # Fighting can be intentional; ineffective combat is handled separately.
    if memory.recent_shots or any(distance(p['eye'],e['p'])<200 and e['type']!=7 for e in s['enemies']):return None
    # The general memory window may still describe a prior pickup/heal.
    # Only reuse its stall/cycle result after this movement task has occupied
    # that whole window; urgent retreat/hazard checks above stay shorter.
    window=26 if memory.movement_problem=='cycling_in_small_area' else 10
    return memory.movement_problem if s['t']-motor.started>=window else None

def reset_finished_wait(s,memory,motor,previous_phase):
    """A completed holdout is progress, not a pre-existing travel stall."""
    current=mission(s)['phase']
    if previous_phase=='defend' and current!='defend':
        memory.reset_motion()
        if motor.task and motor.task.kind=='hold':motor.started=s['t']
    return current

class CombatMonitor:
    def __init__(self):self.target=None;self.started=0;self.hp=0;self.shots=0
    def update(self,s,action):
        target=next((e for e in s['enemies'] if e['id']==action.get('target_id')),None)
        shots=s.get('metrics',{}).get('shots',0)
        if target is None or 'fire' not in action['buttons']:
            if target is None:self.target=None
            return None
        if target['id']!=self.target or target['health']<self.hp:
            self.target=target['id'];self.hp=target['health'];self.started=s['t'];self.shots=shots
        if s['t']-self.started>8 and shots-self.shots>=8:
            return 'Repeated confirmed shots without observed damage to the selected enemy.'
        return None

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--seconds',type=int,default=60);parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if not 1<=args.seconds<=900:raise ValueError('Use a bounded run of at most fifteen minutes')
    control=GameInput(args.pid);jev=None;pending=None;pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    report={'started_utc':stamp(),'pid':args.pid,'controller':'general-v1','status':'starting'}
    memory=EvidenceMemory();started=time.monotonic();last_request=-100;last_status=-100
    decision=None;planner=None;recovery_count=0;recovery_origin=None;reason='Validation interval complete';outside_since=None;last_motor_seq=None;last_area_correction=None
    consecutive_timeouts=0;ledge_since=None;previous_phase=None
    try:
        control.guard();control.release_all()
        if args.resume:
            before=read_json(EMS/'state.json');time.sleep(.3)
            if read_json(EMS/'state.json')['seq']!=before['seq']:raise RuntimeError('Game already advancing; refused pause toggle')
            control.act(keys=['f9'],seconds=.15);time.sleep(.25)
        s=observe()
        if not s.get('metrics') or not s['player'].get('weapons'):raise RuntimeError('General-agent observation schema not deployed')
        nav=Navigation(s['map']);motor=SkillMotor(nav,memory);planner=MissionPlanner(nav,memory);combat=CombatMonitor();jev=JevClient();motion=MotionRecovery(movement_keys)
        report.update(map=s['map'],round_id=s.get('round_id'),start_game_t=s['t'],start_player=s['player'],start_metrics=s['metrics'])
        from vision_observer import VisionInbox
        vision=VisionInbox()
        commentary('Astra','Testing independent movement, targeting and combat decisions with action-outcome checks and visual observations.')
        while time.monotonic()-started<args.seconds:
            now=time.monotonic();s=observe();p=s['player']
            if s['map']!=nav.map or s['outcome']!='active' or s.get('round_id')!=report['round_id']:reason='Chapter or campaign state changed';break
            if p['dead']:reason='Player died; planner review required';break
            if not s['nav_ready']:raise RuntimeError('Navigation data not ready')
            if p.get('ledge'):
                if ledge_since is None:ledge_since=s['t']
                if wait_for_ledge_rescue(s,s['t']-ledge_since):
                    control.sustain(seconds=.08);decision=None;memory.reset_motion();continue
                raise RuntimeError('Ledge rescue did not complete during the bounded teammate wait')
            ledge_since=None
            correction=nav.correct_player_area(s)
            correction_key=(correction['sensor_area'],correction['resolved_area']) if correction else None
            if correction_key and correction_key!=last_area_correction:
                log('agent-navigation-corrections.jsonl',dict(correction,utc=stamp(),map=s['map'],game_t=s['t']))
            last_area_correction=correction_key
            if s['seq']==last_motor_seq:
                # The 10 Hz observer is slower than some motor ticks. Keep
                # existing held controls, but do not apply the same relative
                # camera correction twice to a single observed angle.
                control.sustain(keys=control.held_keys,buttons=control.held_buttons,seconds=.05)
                continue
            if (p['area'] not in nav.areas and not known_ladder_transit(s,nav)) or wait_for_landing(s,nav,0):
                if outside_since is None:outside_since=s['t']
                if (wait_for_landing(s,nav,s['t']-outside_since) or wait_for_elevator(s,s['t']-outside_since)
                    or wait_for_pinned_rescue(s,s['t']-outside_since)):
                    # A genuine fall can briefly lack a usable area. Release
                    # inputs and let gravity/observed transport finish;
                    # never invent a route between navigation floors.
                    control.sustain(seconds=.08);decision=None;continue
                raise RuntimeError('Player outside observed navigation graph')
            outside_since=None
            if memory.previous is None or s['seq']!=memory.previous['seq']:memory.update(s)
            repeated=[key for key,count in memory.failures.items() if count>=2 and key.startswith(('use_','deliver_','carry_'))]
            if repeated:reason='Repeated interaction failed; visual planner handoff: '+repeated[0];break
            planner.update(s);memory.visual_facts=vision.read(s)
            previous_phase=reset_finished_wait(s,memory,motor,previous_phase)
            if getattr(memory,'fuel_landing_problem',None):
                report['misplaced_fuel']=memory.fuel_landing_problem
                reason='Fuel landed outside the reviewed receiving floor; visual planner must correct the throw before continuing';break
            if pending and pending.done():
                result,consecutive_timeouts=completed_request(pending,consecutive_timeouts)
                if result is None:
                    # JevClient already closed the failed connection and kept
                    # its uncertain charge reserved. Release old controls and
                    # obtain a new live packet after a short bounded backoff.
                    log('agent-network-events.jsonl',{'utc':stamp(),'game_t':s['t'],
                        'kind':'transient_request_failure','consecutive':consecutive_timeouts,
                        'uncertain_cost_remains_reserved':True})
                    pending=None;decision=None;last_request=now+.5
                    control.release_all();continue
                candidate=compose(result,context['tasks'],context['state'])
                candidate.update(requested_wall=context['wall'],phase=context['phase'])
                accepted=decision_fresh(candidate,s,now)
                revalidated=usable_decision(candidate,s,nav,memory,now)
                log('agent-decisions.jsonl',{'utc':stamp(),'game_t':s['t'],'input':context['data'],'result':result,
                    'accepted_fresh':accepted,'accepted_after_current_state_check':not accepted and revalidated is not None})
                if revalidated is not None:
                    decision=candidate
                    if candidate['needs_review']:
                        reason='Ambiguous mission action; planner review requested';break
                pending=None
            if pending is None and now-last_request>=.24:
                data,questions,tasks=packet(s,nav,memory)
                context={'state':s,'data':data,'tasks':tasks,'wall':now,'phase':mission(s)['phase']}
                pending=pool.submit(jev.request,data,questions);last_request=now
            active=usable_decision(decision,s,nav,memory,now)
            if active is None and motor.task and motor.task.kind in ('route','escape','evade'):
                motor.task=None
            action=motor.tick(s,active)
            action=motion.adjust(s,action,nav)
            action=motion.avoid_calm_witches(s,action,nav)
            if getattr(memory,'review_each_throw',False) and any(o['task'].startswith('stage_') and o['status']=='input_accepted' for o in memory.outcomes):
                # The release was learned after this tick's planner update.
                # Persist that unverified release before handing off, so the
                # next controller does not mistake its old location for fuel.
                planner.update(s)
                if planner.fuel:planner.fuel.save(s['t'])
                reason='Fuel throw released; visual planner must verify its landing before reuse';break
            no_effect=combat.update(s,action)
            if no_effect:reason=no_effect;break
            problem=motion.blocked_reason or progress_reason(s,memory,motor)
            if recovery_origin and distance(p['p'],recovery_origin)>350:recovery_count=0;recovery_origin=None
            if problem:
                if motor.task and motor.task.kind in ('evade','escape') and recovery_count<2 and motor.escape_goal is not None:
                    alternate=motor.recover_escape(s)
                    if alternate:
                        log('nav-recoveries-v2.jsonl',dict(alternate,utc=stamp(),map=s['map'],game_t=s['t'],reason=problem))
                        recovery_count+=1;recovery_origin=list(p['p']);memory.reset_motion();motion.reset();decision=None
                        memory.outcome(motor.task.kind,'interrupted',s['t'],'Retreat blocked; another observed walkable route selected.',0)
                        control.release_all();continue
                if motor.task and motor.task.kind in ('evade','hold'):
                    reason='Ineffective movement: '+problem+'; visual planner handoff';break
                if recovery_count<2 and not p.get('on_ladder') and nav.avoid_current_edge(s,problem):
                    recovery_count+=1;recovery_origin=list(p['p']);memory.reset_motion();motion.reset();decision=None
                    memory.outcome('route','failed',s['t'],problem+'; alternate observed route selected.')
                    control.release_all();continue
                reason='Ineffective movement: '+problem+'; visual planner handoff';break
            if p['pinned'] or p['incap'] or p.get('immobilized'):memory.reset_motion()
            control.sustain(**{k:action[k] for k in ('keys','buttons','dx','dy')},seconds=.08,
                pulse_movement='shift' in action['keys'])
            last_motor_seq=s['seq']
            if now-last_status>=.5:
                report.update(status='running',updated_utc=stamp(),player=p,game_t=s['t'],metrics=s['metrics'],
                    action=action,outcomes=list(memory.outcomes),jev_spent_usd=jev.spent,vision_active=memory.visual_facts is not None,
                    phase=planner.phase,planner_version=planner.directive_version)
                report['vision_observer']='Astra screenshot review in this thread; no local model'
                report['navigation_area_correction']=correction
                snapshot('agent-status.json',report)
                log('agent-actions.jsonl',{'utc':stamp(),'game_t':s['t'],'p':p['p'],'health':p['health'],'clip':p['clip'],'metrics':s['metrics'],'action':action})
                last_status=now
        report.update(status='handoff',reason=reason,end_game_t=s['t'],end_player=s['player'],end_metrics=s['metrics'],events=s['events'])
    except Exception as error:
        report.update(status='stopped',reason=f'{type(error).__name__}: {error}',error_traceback=traceback.format_exc())
    finally:
        pause_confirmed=False
        try:
            if foreground_pid()==args.pid:
                control.release_all();pause_confirmed=control.emergency_pause()
            a=read_json(EMS/'state.json');time.sleep(.25);b=read_json(EMS/'state.json')
            report['game_clock_frozen']=a['seq']==b['seq']
            report['pause_confirmed_by_input']=bool(pause_confirmed and report['game_clock_frozen'])
            # Astra reviews this actual scene at each bounded run or loop handoff.
            # Capturing it is not a claim that visual review already happened.
            from vision_observer import capture_request
            try:report['visual_review_request']=capture_request(b,live=True)['id']
            except Exception as capture_error:report['visual_capture_error']=str(capture_error)
        except Exception as error:report['pause_error']=str(error)
        try:
            report['game_process_exited']=game_process_exited(args.pid)
            report['recording_finalization']=finalize_recording(control.obs,
                pause_confirmed=report.get('pause_confirmed_by_input',False),
                game_exited=report['game_process_exited'])
        except Exception as error:report['recording_finalization_error']=str(error)
        report['outcomes']=list(memory.outcomes)
        if planner and planner.fuel:
            try:
                planner.fuel.save(s['t']);report['fuel_plan_saved']=True
            except Exception as error:report['fuel_plan_save_error']=str(error)
        pool.shutdown(wait=True,cancel_futures=True)
        if jev:report['jev_spent_usd']=jev.spent;jev.close()
        control.close();report['finished_utc']=stamp();snapshot('agent-status.json',report);log('agent-runs.jsonl',report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('player','start_player','end_player','metrics','start_metrics','end_metrics','outcomes','error_traceback')},indent=2))

if __name__=='__main__':main()
