"""Read local Valve assets for mechanics research. Never execute or alter them."""
import json
import lzma
import pathlib
import re
import struct

ROOT=pathlib.Path(__file__).resolve().parent
from project_paths import GAME_DIR
GAME=GAME_DIR.parent
OUT=ROOT/'private'/'valve-reference'

def entities(path):
    with path.open('rb') as f:
        header=f.read(1036)
        if header[:4]!=b'VBSP': raise ValueError('Not a Source map')
        bsp_version=struct.unpack_from('<i',header,4)[0]
        if bsp_version==21:
            version,offset,length,fourcc=struct.unpack_from('<4i',header,8)
        else: offset,length,version,fourcc=struct.unpack_from('<4i',header,8)
        f.seek(offset);body=f.read(length)
    if body[:4]==b'LZMA':
        actual,compressed=struct.unpack_from('<II',body,4)
        prop=body[12];lc=prop%9;prop//=9;lp=prop%5;pb=prop//5
        dictionary=struct.unpack_from('<I',body,13)[0]
        body=lzma.decompress(body[17:17+compressed],format=lzma.FORMAT_RAW,
            filters=[{'id':lzma.FILTER_LZMA1,'dict_size':dictionary,'lc':lc,'lp':lp,'pb':pb}])
        if len(body)!=actual: raise ValueError('Invalid entity lump length')
    rows=[]
    for block in re.findall(r'\{([^{}]*)\}',body.decode('utf-8','replace')):
        pairs=re.findall(r'"([^"\n]*)"\s*"([^"\n]*)"',block)
        row={};outputs=[]
        for key,value in pairs:
            if key.startswith('On'): outputs.append([key,value])
            else: row[key]=value
        if outputs: row['outputs']=outputs
        rows.append(row)
    if not rows or rows[0].get('classname')!='worldspawn': raise ValueError('Entity lump did not begin with worldspawn')
    return rows

def main():
    OUT.mkdir(parents=True,exist_ok=True);index=[]
    for path in sorted((GAME/'left4dead2'/'maps').glob('c*m*.bsp')):
        if not re.match(r'c[1-5]m\d+_',path.name): continue
        rows=entities(path)
        relevant=[r for r in rows if r.get('classname') in {
            'func_button','func_button_timed','trigger_finale','point_prop_use_target',
            'point_script_use_target','weapon_scavenge_item_spawn','weapon_cola_bottles_spawn',
            'weapon_gascan_spawn','prop_door_rotating_checkpoint','env_instructor_hint',
            'func_elevator','logic_choreographed_scene'} or
            any(w in r.get('targetname','').lower() for w in ['cola','gascannozzle','radio','finale','escape','alarm','fuel'])]
        target=OUT/(path.stem+'-objectives.json')
        target.write_text(json.dumps(relevant,indent=2),encoding='utf-8')
        index.append({'map':path.stem,'entities':len(rows),'relevant_entities':len(relevant),'file':target.name})
    (OUT/'map-index.json').write_text(json.dumps(index,indent=2))
    print(json.dumps(index))

if __name__=='__main__':main()
