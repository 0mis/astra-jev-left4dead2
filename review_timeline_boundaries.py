"""Sample draft edit boundaries and story continuity; not full privacy review."""
import datetime as dt
import json
from pathlib import Path
import av
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'private/postproduction/timeline-samples'


def frame_at(video,t):
    with av.open(str(video)) as c:
        v=c.streams.video[0];v.codec_context.thread_count=1
        c.seek(int(max(0,t)/v.time_base),stream=v,backward=True)
        for f in c.decode(v):
            if f.time is not None and f.time>=t-.02:return f.time,f.to_image()
    raise ValueError(f'Frame unavailable at {video.name} {t}')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    draft=json.loads((ROOT/'private/postproduction/draft-timeline.json').read_text())
    allrows=[]
    for chapter in draft['chapters']:
        rows=[];tiles=[]
        for clip in chapter['clips']:
            a,b=clip['source_start'],clip['source_end']
            times={a+.05,b-.05};t=a+30
            while t<b-.05:times.add(t);t+=60
            for t in sorted(times):
                actual,im=frame_at(ROOT/'recordings'/clip['file'],t)
                im.thumbnail((400,225));tile=Image.new('RGB',(400,265),'#151515');tile.paste(im,(0,40))
                d=ImageDraw.Draw(tile)
                d.text((5,3),clip['file'],fill='white');d.text((5,20),f'{actual:.2f} sec | {chapter["map"]}',fill='white')
                rows.append({'file':clip['file'],'source_second':round(actual,6),'map':chapter['map'],'kind':'sample'})
                tiles.append(tile)
        for page in range((len(tiles)+23)//24):
            subset=tiles[page*24:(page+1)*24];sheet=Image.new('RGB',(1600,1590),'#151515')
            for i,im in enumerate(subset):sheet.paste(im,((i%4)*400,(i//4)*265))
            filename=f'{chapter["map"]}-{page+1:02}.jpg';sheet.save(OUT/filename,quality=91)
            for i,row in enumerate(rows[page*24:(page+1)*24]):row.update(sheet=filename,tile=i)
        allrows.extend(rows)
        (OUT/'index.json').write_text(json.dumps({'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'coverage':'Selected boundaries and 60-second interior samples only. Not full audiovisual review.',
            'samples':allrows},indent=2),encoding='utf-8')
        print(json.dumps({'map':chapter['map'],'samples':len(rows),'total':len(allrows)}),flush=True)


if __name__=='__main__':main()
