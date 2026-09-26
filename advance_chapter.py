"""Record a verified ordinary chapter transition without menu shortcuts."""
import argparse
import datetime
import pathlib
import time
import json
from observe_game import EMS,read_json
from obs_guard import client,audit,finalize_recording
from game_input import GameInput
from vision_observer import atomic

ROOT=pathlib.Path(__file__).resolve().parent
CAMPAIGNS={
    'Dead Center':['c1m1_hotel','c1m2_streets','c1m3_mall','c1m4_atrium'],
    'Dark Carnival':['c2m1_highway','c2m2_fairgrounds','c2m3_coaster','c2m4_barns','c2m5_concert'],
    'Swamp Fever':['c3m1_plankcountry','c3m2_swamp','c3m3_shantytown','c3m4_plantation'],
    'Hard Rain':['c4m1_milltown_a','c4m2_sugarmill_a','c4m3_sugarmill_b','c4m4_milltown_b','c4m5_milltown_escape'],
    'The Parish':['c5m1_waterfront','c5m2_park','c5m3_cemetery','c5m4_quarter','c5m5_bridge'],
}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pid',required=True,type=int)
    parser.add_argument('--from-map',required=True);args=parser.parse_args()
    campaign,maps=next((k,v) for k,v in CAMPAIGNS.items() if args.from_map in v[:-1])
    destination=maps[maps.index(args.from_map)+1]
    s=read_json(EMS/'state.json');time.sleep(.3)
    assert s['seq']==read_json(EMS/'state.json')['seq'],'Game must already be paused'
    assert s['map']==args.from_map and any(e['kind']=='map_transition' and e.get('map')==s['map'] for e in s['events'])
    assert read_json(ROOT/'agent-status.json')['status']!='running','Controller must be terminal'
    evidence='private/'+s['map'].split('_')[0]+'-completion-state.json'
    atomic(ROOT/evidence,s)
    progress=read_json(ROOT/'campaign-progress.json');now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not any(r['map']==s['map'] for r in progress['campaigns'][campaign]['chapters']):
        progress['campaigns'][campaign]['chapters'].append({'map':s['map'],'verified_utc':now,
            'outcome':'map_transition','events':s['events'],'game_t':s['t'],'health':s['player']['health'],
            'winning_round':s['round_id'],'evidence':evidence})
        progress['updated_utc']=now;atomic(ROOT/'campaign-progress.json',progress)
    c=client();assert not audit(c)['recording']['outputActive']
    c.send('StartRecord',raw=True);time.sleep(1.2);a=audit(c,require_recording=True)
    time.sleep(1.1);b=audit(c,require_recording=True)
    assert b['recording']['outputDuration']>a['recording']['outputDuration']
    control=GameInput(args.pid);paused=False;ready=False
    result={'from_map':s['map'],'destination':destination}
    try:
        control.act(keys=['f9'],seconds=.15);until=time.monotonic()+60
        while time.monotonic()<until:
            control.act(seconds=.5);fresh=read_json(EMS/'state.json')
            if (fresh['map']==destination and fresh.get('nav_ready') and fresh['t']>5
                and time.time()-(EMS/'state.json').stat().st_mtime<1):
                ready=True;break
        paused=control.emergency_pause();fresh=read_json(EMS/'state.json')
        result.update(state=fresh,ready=ready,pause_confirmed=paused)
    finally:
        control.close();result['recording_finalization']=finalize_recording(c,pause_confirmed=paused)
        atomic(ROOT/'private'/('transition-to-'+destination+'.json'),result)
    print(json.dumps({'ready':ready,'pause_confirmed':paused,'map':fresh['map'],
        't':fresh['t'],'health':fresh['player']['health'],'area':fresh['player']['area'],
        'recording_finalization':result['recording_finalization']}))
    if not ready or not paused:raise RuntimeError('Transition requires live review')

if __name__=='__main__':main()
