"""Private draft timeline: merge logged winning intervals and remove pause labels.

This is an editing proposal, never publication approval. Exact story/transition
boundaries, missing helper captures and audiovisual privacy still need review.
Original recordings are only read.
"""
import datetime as dt
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
POST=ROOT/'private/postproduction'


def merged(intervals):
    result=[]
    for start,end in sorted(intervals):
        if end<=start:continue
        if result and start<=result[-1][1]+.001:
            result[-1][1]=max(result[-1][1],end)
        else:result.append([start,end])
    return result


def subtract(intervals,cuts):
    result=[]
    for start,end in intervals:
        cursor=start
        for left,right in cuts:
            if right<=cursor:continue
            if left>=end:break
            if left>cursor:result.append([cursor,min(left,end)])
            cursor=max(cursor,right)
            if cursor>=end:break
        if cursor<end:result.append([cursor,end])
    return result


def build():
    draft=json.loads((POST/'successful-attempt-edit-candidates.json').read_text())
    helperfile=POST/'ending-association/helper-candidates.json'
    helpers=json.loads(helperfile.read_text()) if helperfile.exists() else []
    exclusion_path=POST/'source-exclusions.json'
    exclusions=json.loads(exclusion_path.read_text()).get('sources',{}) if exclusion_path.exists() else {}
    addition_path=POST/'chapter-additions.json'
    additions=json.loads(addition_path.read_text()).get('additions',[]) if addition_path.exists() else []
    chapters=[];pending=[];clock=0
    for chapter in draft['chapters']:
        files={}
        for clip in chapter['candidates']:
            if not clip['candidate_source_seconds']:
                pending.append({'map':chapter['map'],'file':clip['file'],'reason':'missing source boundaries'})
                continue
            files.setdefault(clip['file'],[]).append(clip['candidate_source_seconds'])
        if chapter['verified_outcome']=='finale_win':
            for helper in helpers:
                proposal=helper.get('boundary_proposal',{}).get('proposed_append')
                if helper['campaign']==chapter['campaign'] and proposal:
                    files.setdefault(helper['source'],[]).append(proposal)
        for addition in additions:
            if addition['map']!=chapter['map']:
                continue
            source=ROOT/'recordings'/addition['file'];stat=source.stat()
            if stat.st_size!=addition['source_bytes'] or stat.st_mtime_ns!=addition['source_mtime_ns']:
                raise RuntimeError('Reviewed addition source changed: '+addition['file'])
            files.setdefault(addition['file'],[]).append(addition['source_seconds'])
        clips=[];before=0;after=0
        for filename,intervals in sorted(files.items()):
            intervals=merged(intervals);before+=sum(b-a for a,b in intervals)
            scanpath=POST/'pause-scans'/(Path(filename).stem+'.json')
            if not scanpath.exists():
                pending.append({'map':chapter['map'],'file':filename,'reason':'pause scan missing'})
                continue
            scan=json.loads(scanpath.read_text());source=ROOT/'recordings'/filename
            stat=source.stat()
            if stat.st_size!=scan.get('source_bytes') or stat.st_mtime_ns!=scan.get('source_mtime_ns'):
                pending.append({'map':chapter['map'],'file':filename,'reason':'source differs from scanned version'})
                continue
            # Pad by three frames to avoid including the pause-label fade.
            cuts=merged([[max(0,a-.1),b+.1] for a,b in scan['pause_candidates']])
            source_exclusion=exclusions.get(filename)
            if source_exclusion:
                if (stat.st_size!=source_exclusion['source_bytes'] or
                        stat.st_mtime_ns!=source_exclusion['source_mtime_ns']):
                    raise RuntimeError('Reviewed exclusion source changed: '+filename)
                cuts=merged(cuts+[row['source_seconds'] for row in source_exclusion['cuts']])
            for start,end in subtract(intervals,cuts):
                if end-start<.2:continue
                duration=end-start
                clips.append({'file':filename,'source_start':round(start,6),'source_end':round(end,6),
                    'timeline_start':round(clock,6),'timeline_end':round(clock+duration,6),
                    'selection':'draft recording associations and reviewed additions minus padded pauses and source exclusions',
                    'source_exclusion_evidence':'source-exclusions.json' if source_exclusion else None,
                    'source_addition_evidence':'chapter-additions.json' if any(a['map']==chapter['map'] and a['file']==filename and a['source_seconds'][0]<end and a['source_seconds'][1]>start for a in additions) else None,
                    'boundary_review':'pending','privacy_review':'pending','audio_review':'pending'})
                clock+=duration;after+=duration
        chapters.append({'campaign':chapter['campaign'],'map':chapter['map'],
            'winning_round':chapter['winning_round'],'manual_association_needed':chapter['manual_association_needed'],
            'candidate_seconds_before_pause_removal':round(before,3),
            'draft_seconds_after_pause_removal':round(after,3),'clips':clips,
            'helper_ending_and_transition_coverage':'pending'})
    report={'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'draft_only':True,
        'ready_to_publish':False,'runtime_seconds':round(clock,3),'chapters':chapters,'pending_sources':pending,
        'full_export_review':'not started','note':'No original files edited; this is not proof of complete story or privacy coverage.'}
    (POST/'draft-timeline.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'chapters':len(chapters),'clips':sum(len(c['clips']) for c in chapters),
        'draft_hours':round(clock/3600,3),'pending_sources':len(pending),'ready_to_publish':False}))


if __name__=='__main__':build()
