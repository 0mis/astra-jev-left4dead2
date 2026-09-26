"""One bounded, recorded ordinary input for an inspected recovery point."""
import argparse
import json
import re
import time
from observe_game import EMS,read_json,observe
from obs_guard import ROOT,client,audit,finalize_recording
from game_input import GameInput,SCANS
from vision_observer import atomic,capture_request


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pid',type=int,required=True)
    ap.add_argument('--name',required=True);ap.add_argument('--keys',nargs='*',choices=SCANS,default=[])
    ap.add_argument('--dx',type=int,default=0);ap.add_argument('--dy',type=int,default=0)
    ap.add_argument('--seconds',type=float,default=.3);args=ap.parse_args()
    if not re.fullmatch('[a-z0-9-]+',args.name):raise ValueError('Invalid private evidence name')
    if not .05<=args.seconds<=1:raise ValueError('Single input duration outside bound')
    before=read_json(EMS/'state.json');time.sleep(.4)
    if before['seq']!=read_json(EMS/'state.json')['seq']:raise RuntimeError('Recovery expects a verified paused game')
    if read_json(ROOT/'agent-status.json')['status']=='running':raise RuntimeError('Controller must be terminal')
    c=client();existing=audit(c)['recording']['outputActive']
    if not existing:c.send('StartRecord',raw=True)
    time.sleep(1.2);a=audit(c,require_recording=True);time.sleep(1.1);b=audit(c,require_recording=True)
    if b['recording']['outputDuration']<=a['recording']['outputDuration']:raise RuntimeError('Capture is not advancing')
    g=GameInput(args.pid);paused=False;r={'before':before,'inputs':vars(args),'scope':'single ordinary recovery input'}
    try:
        g.act(keys=['f9'],seconds=.15)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            fresh=read_json(EMS/'state.json')
            if fresh['seq']!=before['seq']:break
            time.sleep(.1)
        else:raise RuntimeError('Observer did not resume; no movement input issued')
        g.act(keys=args.keys,dx=args.dx,dy=args.dy,seconds=args.seconds)
        g.act(seconds=.35)
    finally:
        try:
            paused=g.emergency_pause();fresh=read_json(EMS/'state.json')
            r.update(after=fresh,pause_confirmed=paused)
            v=capture_request(fresh,live=True);r['image']=v['file']
        finally:
            g.close();r['recording_finalization']=finalize_recording(c,pause_confirmed=paused)
            atomic(ROOT/'private'/(args.name+'.json'),r)
    print(json.dumps({'paused':paused,'t':fresh['t'],'p':fresh['player']['p'],'hp':fresh['player']['health'],
        'ledge':fresh['player']['ledge'],'area':fresh['player']['area'],'nearest_area':fresh['player']['nearest_area'],
        'image':r['image']}))


if __name__=='__main__':main()
