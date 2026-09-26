"""Read only the allowlisted local sensor output; never send account identifiers."""
import json
import math
import pathlib
import time
from project_paths import GAME_DIR
EMS=GAME_DIR/'ems'/'astra'

def read_json(path):
    # StringToFile emits one trailing NUL. Retry only an incomplete local write.
    for attempt in range(3):
        try: return json.loads(path.read_bytes().rstrip(b'\0'))
        except (json.JSONDecodeError,PermissionError):
            if attempt==2: raise
            time.sleep(.015)

def filter_inactive_enemies(state):
    # Keep the raw observation auditable, but do not spend movement or
    # ammunition on an enemy the engine reports as incapacitated.
    state['inactive_enemies']=[e for e in state.get('enemies',[]) if e.get('incap',False)]
    state['enemies']=[e for e in state.get('enemies',[]) if not e.get('incap',False)]
    return state

def observe(*,max_age=2.0,check_privacy=True):
    path=EMS/'state.json'
    if time.time()-path.stat().st_mtime>max_age: raise RuntimeError('Game telemetry stale or paused')
    state=read_json(path)
    if state.get('version')!=1: raise RuntimeError('Unknown telemetry schema')
    if any(not math.isfinite(float(x)) for x in state['player']['p']+state['player']['angles']):
        raise RuntimeError('Invalid player coordinates')
    if check_privacy:
        if state['privacy']['sv_cheats']!=0: raise RuntimeError('Cheats enabled; normal run refused')
        if not state['privacy']['voice_disabled']: raise RuntimeError('Voice not confirmed disabled')
        if not state['privacy']['private_lan']: raise RuntimeError('Local-only server not confirmed')
        if any(not p['bot'] for p in state['teammates']): raise RuntimeError('Unexpected other human player')
    return filter_inactive_enemies(state)

if __name__=='__main__':
    print(json.dumps(observe(),indent=2))
