"""Jev selects bounded actions; local feedback applies ordinary game inputs.

Stops for stale state, capture failure, budget exhaustion, chapter transitions,
or lack of progress. This controller never advances campaign state by command.
"""
import argparse
import collections
import datetime
import json
import math
import time
from game_input import GameInput,foreground_pid
from jev_bridge import JevClient,commentary
from navigation import Navigation,distance,flat_distance,look,wrap
from observe_game import EMS,observe,read_json
from obs_guard import ROOT

def stamp(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def log(name,value):
    with (ROOT/name).open('a',encoding='utf-8') as f: f.write(json.dumps(value,separators=(',',':'))+'\n')

def options(state,nav,attempts):
    p=state['player']; actions={}; texts={}
    def offer(key,text,kind,target=None):
        actions[key]={'kind':kind,'target':target};texts[key]=text
    waypoint=nav.waypoint(state)
    offer('advance','Follow the next route segment toward the exit, opening an observed closed door if it blocks the route.','advance',waypoint)
    if waypoint.get('link_type')=='ladder':
        texts['advance']='Approach the usable ladder, face it and climb, then step onto its landing. Continue until the climb is complete.'
        if p.get('on_ladder') and not (p['pinned'] or p['dead']): return actions,texts,waypoint
    if state.get('exit_checkpoint_occupied') and distance(p['p'],nav.areas[nav.goal]['p'])<600:
        del actions['advance'];del texts['advance']
    if waypoint.get('link_type')=='elevator':
        texts['advance']='Board or stay centered on the elevator platform while it travels. Walking into its walls does not reach the next floor.'
        if flat_distance(p['p'],waypoint['p'])<12:
            del actions['advance'];del texts['advance']
        offer('wait_elevator','Wait briefly inside the elevator for the bots or moving platform.','wait')
        for button in state.get('interactables',[]):
            if button['type']=='func_button' and 'elevator' in button.get('name','').lower() and abs(button['p'][2]-p['eye'][2])<100 and attempts[button['id']]<8:
                offer('use_'+str(button['id']),'Reach and press the observed elevator button using normal Use. This activates the lift; all survivors may need to be aboard.','use',button)
    inventory=p['inventory']
    usable={i['id']:i for i in state['items']+state.get('interactables',[])}
    for item in sorted(usable.values(),key=lambda x:distance(x['p'],p['p'])):
        if attempts[item['id']]>8: continue
        kind=item['type']; d=round(distance(item['p'],p['p']))
        desired=('first_aid_kit_spawn' in kind and 'slot3' not in inventory)
        desired|=(kind=='weapon_pistol_spawn' and p['weapon']=='weapon_pistol' and p['clip']<=15 and attempts[item['id']]<4)
        desired|=(any(g in kind for g in ['smg','shotgun','rifle']) and kind.endswith('_spawn') and 'slot0' not in inventory)
        desired|=(kind=='weapon_spawn' and any(g in item.get('model','') for g in ['smg','shotgun','rifle']) and 'slot0' not in inventory)
        if desired: offer('loot_'+str(item['id']),f'Collect {kind} at distance {d} through ordinary movement and Use.','loot',item)
        if d<160 and ('door' in kind or kind in ['func_button','point_prop_use_target']):
            offer('use_'+str(item['id']),f'Use the nearby {kind}, distance {d}, if it blocks progress.','use',item)
        if kind=='prop_door_rotating_checkpoint' and item.get('door_state')==2 and state.get('exit_checkpoint_occupied'):
            offer('close_safe_room_door','Close the exit safe-room door with normal Use once the team is inside, completing this chapter.','use',item)
    enemies=sorted(state['enemies'],key=lambda e:(0 if e['type'] else 1,distance(e['p'],p['p'])))
    names={0:'common infected',1:'Smoker',2:'Boomer',3:'Hunter',4:'Spitter',5:'Jockey',6:'Charger',7:'Witch',8:'Tank'}
    for e in enemies[:6]:
        offer('shoot_'+str(e['id']),f'Aim and fire at visible {names.get(e["type"],"infected")}, distance {round(distance(e["p"],p["p"]))}, health {e["health"]}.','shoot',e)
    nearby=sorted((e for e in enemies if distance(e['p'],p['p'])<160 and e['type']!=7),key=lambda e:distance(e['p'],p['p']))
    if nearby: offer('shove','An infected is within striking distance. Shove this nearest attacker away, then shoot it.','shove',nearby[0])
    if 0<=p['clip']<5: offer('reload','Reload the current weapon because its magazine is low.','reload')
    if p['health']+p['temp_health']<50 and inventory.get('slot3')=='weapon_first_aid_kit': offer('heal','Use the carried first aid kit to heal while bots provide cover.','heal')
    if p['health']+p['temp_health']<60 and inventory.get('slot4') in ('weapon_pain_pills','weapon_adrenaline'):
        offer('boost','Use carried pills or adrenaline for an immediate temporary health boost.','boost')
    if p['on_fire'] or p['area_damaging']:
        texts['advance']='URGENT: leave the damaging fire area along the safer route immediately; do not stop here to fight or heal.'
    if p['immobilized'] and not p['incap'] and not (p['on_fire'] or p['area_damaging']):
        return {'wait':{'kind':'wait','target':None}},{'wait':'Wait briefly for the current healing or recovery animation to finish.'},waypoint
    if p.get('dominator_type')==5:
        bots=[b for b in state['teammates'] if not b['dead'] and not b['pinned'] and not b['incap']]
        if bots:
            b=min(bots,key=lambda b:distance(b['p'],p['p']))
            return {'resist_jockey':{'kind':'resist','target':b}},{'resist_jockey':'Counter-steer toward the nearest free teammate so they can knock the Jockey off. Ordinary movement remains possible while ridden.'},waypoint
    if p['pinned'] or p['ledge'] or p['dead']: return {'wait':{'kind':'wait','target':None}},{'wait':'Wait briefly for bot assistance; character cannot act normally.'},waypoint
    if nearby and not p['incap'] and not (p['on_fire'] or p['area_damaging']):
        # Route following cannot succeed while an infected is body-blocking us.
        # Jev still chooses the target, firing, shove, or reload response.
        actions={k:v for k,v in actions.items() if v['kind'] in ('shoot','shove','reload','boost')}
        texts={k:texts[k] for k in actions}
    return actions,texts,waypoint

def safe_fire(state,target):
    eye=state['player']['eye']; ray=[b-a for a,b in zip(eye,target)]
    rr=sum(v*v for v in ray)
    if rr<1: return False
    for bot in state['teammates']:
        if bot['dead']: continue
        center=[bot['p'][0],bot['p'][1],bot['p'][2]+40]
        v=[b-a for a,b in zip(eye,center)]
        t=sum(a*b for a,b in zip(v,ray))/rr
        if 0<t<1 and math.sqrt(sum((v[i]-t*ray[i])**2 for i in range(3)))<26: return False
    return True

def execute(control,choice,nav):
    kind=choice['kind']; target=choice['target']
    if kind in ('shoot','reload'):
        p=observe()['player']
        if p['weapon'] not in (p['inventory'].get('slot0'),p['inventory'].get('slot1')) or p['clip']<0:
            slot='1' if 'slot0' in p['inventory'] else '2'
            control.act(keys=[slot],seconds=.1)
            # Equipping is an action of its own; never fire a medicine item.
            return
    if kind=='reload': control.act(keys=['r'],seconds=.15); return
    if kind=='wait': time.sleep(.5); return
    if kind=='boost':
        control.act(keys=['5'],seconds=.1)
        control.hold_fire(2.5,lambda:observe()['player']['inventory'].get('slot4') in ('weapon_pain_pills','weapon_adrenaline'))
        return
    if kind=='heal':
        control.act(keys=['4'],seconds=.1)
        def healing_possible():
            p=observe()['player']
            return not (p['dead'] or p['pinned'] or p['incap']) and p['inventory'].get('slot3')=='weapon_first_aid_kit'
        control.hold_fire(6,healing_possible)
        return
    for _ in range(6):
        s=observe();p=s['player']
        if p['dead'] or (p['pinned'] and kind!='resist') or p['ledge']: return
        keys=[];buttons=[];desired_pitch=0
        if kind=='advance':
            target=nav.waypoint(s); point=target['p'];desired_pitch=target.get('pitch',5)
            angle=look(p['eye'],[point[0],point[1],p['eye'][2]])
        else:
            group=s['teammates'] if kind=='resist' else s['enemies'] if kind in ('shoot','shove') else s['items']+s.get('interactables',[])
            current=next((e for e in group if e['id']==target['id']),None)
            if current is None: return
            point=current['p']
            if kind in ('loot','use') and distance(p['eye'],point)>100:
                target_area=nav.closest_area(point)
                if p['area']!=target_area: point=nav.waypoint(s,target_area)['p']
            angle=look(p['eye'],point);desired_pitch=angle[0]
        yaw_error=wrap(angle[1]-p['angles'][1]);pitch_error=desired_pitch-p['angles'][0]
        yaw_step=max(-70,min(70,yaw_error));pitch_step=max(-45,min(45,pitch_error))
        dx=round(-yaw_step/.066);dy=round(pitch_step/.066)
        if kind in ('advance','loot','use'):
            stopping=6 if kind=='advance' or (kind in ('loot','use') and point!=current['p']) else 68
            if flat_distance(p['p'],point)>stopping and abs(yaw_error)<40: keys.append('w')
            if kind=='advance' and target.get('link_type')=='ladder' and target.get('phase')=='climb' and abs(yaw_error)<40 and 'w' not in keys:
                keys.append('w')
            if kind in ('loot','use') and point==current['p'] and current.get('visible',True) and distance(p['eye'],point)<105 and abs(yaw_error)<15 and abs(pitch_error)<15:
                keys.append('e')
            if kind=='advance' and target.get('link_type')!='ladder' and target['p'][2]-p['p'][2]>22 and flat_distance(p['p'],point)<90:
                keys.append('space')
            if kind=='advance' and (p.get('on_fire') or p.get('area_damaging')):
                if 'space' not in keys: keys.append('space')
            if kind=='advance':
                for door in s['items']:
                    if (door.get('door_state')==0 or door['type']=='func_button') and distance(p['eye'],door['p'])<100:
                        da=look(p['eye'],door['p'])
                        if abs(wrap(da[1]-p['angles'][1]))<25 and abs(da[0]-p['angles'][0])<30:
                            keys.append('e');break
        elif kind=='shoot':
            if p['clip']==0: keys.append('r')
            elif abs(yaw_error)<5 and abs(pitch_error)<6 and safe_fire(s,point): buttons.append('fire')
        elif kind=='shove': buttons.append('shove')
        elif kind=='resist':
            if not p['pinned']: return
            if abs(yaw_error)<55: keys.append('w')
        # Quick ordinary-input shove protects against a pounce between decisions.
        if not p['pinned'] and not p['incap']:
            for enemy in s['enemies']:
                if enemy['type'] in (1,3,5) and distance(p['eye'],enemy['p'])<155:
                    ea=look(p['eye'],enemy['p'])
                    if abs(wrap(ea[1]-p['angles'][1]))<55:
                        if 'shove' not in buttons: buttons.append('shove')
                        break
        control.act(keys=keys,buttons=buttons,dx=dx,dy=dy,seconds=.1)
        if 'e' in keys and kind in ('loot','use'): return

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--seconds',type=int,default=30);parser.add_argument('--resume',action='store_true')
    args=parser.parse_args();control=GameInput(args.pid);jev=None
    started=time.monotonic();last_combat=started;attempts=collections.Counter();history=collections.deque();reason='pilot duration reached';local_recoveries=0;recovery_origin=None
    report={'started_utc':stamp(),'pid':args.pid,'status':'running'}
    try:
        control.guard()
        control.release_all()
        if args.resume:
            before=read_json(EMS/'state.json');time.sleep(.3)
            if read_json(EMS/'state.json')['seq']!=before['seq']: raise RuntimeError('Resume requested but game is already running')
            control.act(keys=['f9'],seconds=.15);time.sleep(.25)
        state=observe();nav=Navigation(state['map']);jev=JevClient()
        report['map']=state['map'];report['start_player']=state['player'];report['start_game_t']=state['t']
        commentary('Astra',f"Chapter {state['map']}: collect supplies, stay with the bots, and follow the escape route.")
        while time.monotonic()-started<args.seconds:
            state=observe()
            if state['map']!=nav.map or state['outcome']!='active':
                report.update(observed_map=state['map'],outcome=state['outcome'],events=state['events'],end_game_t=state['t'])
                reason='chapter or campaign state changed';break
            if not state['nav_ready']: raise RuntimeError('Navigation data not ready')
            if state['player']['dead']: reason='player died; planner review required';break
            actions,criteria,waypoint=options(state,nav,attempts)
            public_state={'game':'Left 4 Dead 2','objective':'Collect needed supplies and reach the chapter exit. Keep moving; fight immediate threats, rescue teammates, conserve health.',
                          'player':state['player'],'visible_enemies':state['enemies'][:12],'teammates':state['teammates'],
                          'next_waypoint':waypoint,'nearby_items_and_doors':state['items'],
                          'interactables':state.get('interactables',[]),
                          'nearby_ladders':state.get('ladders',[]),
                          'exit_checkpoint_occupied':state.get('exit_checkpoint_occupied',False),
                          'obstacle_clearance':state['obstacles'],
                          'recent_choices':[h[4] for h in list(history)[-6:]],
                          'recent_position_change':0 if not history else distance(state['player']['p'],history[0][1])}
            if state.get('exit_checkpoint_occupied'):
                public_state['objective']='The team has reached the exit safe room. Get a missing medkit and primary weapon from nearby supplies, then close the checkpoint door to complete the chapter. Do not return to the room center between pickup steps.'
            public_state['close_attackers']=[{'id':e['id'],'type':e['type'],'distance':round(distance(e['p'],state['player']['p']))} for e in state['enemies'] if distance(e['p'],state['player']['p'])<220]
            public_state['holding_firearm']=state['player']['clip']>=0
            result=jev.request(public_state,{'action':{'type':'choice','instructions':'Choose the most useful immediate action. Fight infected that are close enough to hit or block us: shoot or shove instead of repeatedly trying to walk through them. Shove nearby Jockeys and Hunters before they pounce; kill special infected first. Escape damaging fire before fighting or healing. Get a first aid kit when available and missing unless under immediate attack. When the route is clear, advance. Never repeat an ineffective action or collect an item already owned.','criteria':criteria}})
            selected=result['answers']['action']['choice']; action=actions[selected]
            if action['kind'] in ('loot','use') and distance(state['player']['eye'],action['target']['p'])<110:
                attempts[action['target']['id']]+=1
            log('decisions.jsonl',{'utc':stamp(),'game_t':state['t'],'seq':state['seq'],'state':public_state,'choice':selected,'result':result})
            execute(control,action,nav)
            current=observe();p=current['player']
            if recovery_origin and distance(p['p'],recovery_origin)>300:
                local_recoveries=0;recovery_origin=None
            before=state['player']
            rounds=max(0,before['clip']-p['clip']) if before['weapon']==p['weapon'] and before['clip']>=0 and p['clip']>=0 else 0
            if action['kind'] in ('shoot','shove','reload') or rounds:
                log('combat-checks.jsonl',{'utc':stamp(),'game_t':current['t'],'choice':selected,'kind':action['kind'],
                    'weapon_before':before['weapon'],'weapon_after':p['weapon'],'clip_before':before['clip'],
                    'clip_after':p['clip'],'rounds_observed':rounds,'health':p['health']})
            if current['enemies'] or action['kind'] in ('shoot','shove','reload'): last_combat=time.monotonic()
            if p['incap'] or p['immobilized'] or p['pinned']: history.clear()
            history.append((time.monotonic(),p['p'],p['flow'],json.dumps(p['inventory'],sort_keys=True),selected))
            while history and time.monotonic()-history[0][0]>30: history.popleft()
            report.update(updated_utc=stamp(),player=p,decision=selected,jev_spent_usd=jev.spent,elapsed_seconds=time.monotonic()-started,
                          end_game_t=current['t'],outcome=current['outcome'],events=current['events'])
            (ROOT/'controller-status.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            if len(history)>8 and history[-1][0]-history[0][0]>10 and time.monotonic()-last_combat>8 and not current['enemies'] and not (p['immobilized'] or p['incap'] or p['pinned']):
                radius=max(distance(p['p'],h[1]) for h in history)
                cycling=history[-1][0]-history[0][0]>25 and radius<250
                if (radius<40 or cycling) and len({h[3] for h in history})==1:
                    if action['kind']=='advance' and local_recoveries<3 and nav.avoid_current_edge(current,'Observed ten seconds of ineffective route movement; try an alternate walkable connection.'):
                        local_recoveries+=1;recovery_origin=list(p['p']);history.clear()
                        commentary('Astra','That route connection did not work. Trying an alternate path around the obstacle.')
                        continue
                    reason='No movement or inventory progress for ten seconds; planner handoff';break
        report['status']='handoff';report['reason']=reason
    except Exception as error:
        report.update(status='stopped',reason=f'{type(error).__name__}: {error}')
    finally:
        # F9 toggles a verified local engine pause, not the cosmetic menu.
        try:
            a=read_json(EMS/'state.json');time.sleep(.2);b=read_json(EMS/'state.json')
            if b['seq']!=a['seq'] and foreground_pid()==args.pid:
                control.emergency_pause();time.sleep(.25)
            c=read_json(EMS/'state.json');time.sleep(.2);d=read_json(EMS/'state.json')
            report['game_clock_frozen']=c['seq']==d['seq']
        except Exception as error: report['pause_error']=str(error)
        if jev: jev.close()
        control.close();report['finished_utc']=stamp()
        (ROOT/'controller-status.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        log('run-events.jsonl',report);print(json.dumps(report,indent=2))

if __name__=='__main__': main()
