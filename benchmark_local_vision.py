"""Offline evaluation on visually inspected, private game-only images."""
import argparse
import hashlib
import json
import shutil
from obs_guard import ROOT
from local_vision import analyze
from vision_observer import VISION,atomic

FIXTURES=[
    ('acid_combat',VISION/'b5d9af70-cf23-421d-9021-225a7b63cbb7.png',
        {'scene':'game','ground_hazard':'acid','enemies':'visible'}),
    ('wall_pickup',VISION/'9d329687-1772-48a2-838f-7b850f35639b.png',
        {'scene':'game','ground_hazard':'none','obstacle':'wall_or_fence','enemies':'none'}),
    ('elevator',ROOT/'private'/'masked-scene-check.png',
        {'scene':'game','ground_hazard':'none','enemies':'none'}),
]

def main():
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--width',type=int,default=672)
    a=p.parse_args();results=[]
    for name,path,expected in FIXTURES:
        local=VISION/('benchmark-'+name+'.png')
        if not local.exists():shutil.copyfile(path,local)
        req={'id':'benchmark-'+name,'file':local.name,'sha256':hashlib.sha256(local.read_bytes()).hexdigest(),
             'live':False,'source':'existing inspected game-only capture; offline test, never publish'}
        try:
            r=analyze(req,a.model,width=a.width,publish=False,timeout=90)
            r.update(fixture=name,expected=expected,correct={k:r['labels'][k]==v for k,v in expected.items()},
                     no_false_ladder=r['labels']['obstacle']!='ladder' and r['labels']['interaction']!='ladder')
        except Exception as error:r={'fixture':name,'error':str(error),'model':a.model,'width':a.width}
        print(json.dumps(r),flush=True);results.append(r)
        atomic(VISION/('benchmark-'+a.model.replace(':','-')+'-'+str(a.width)+'.json'),results)

if __name__=='__main__':main()
