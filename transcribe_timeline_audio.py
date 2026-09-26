"""Offline ASR of every selected draft audio interval, with explicit limits.

This helps locate private speech and create captions. ASR may miss/mishear
speech and is not continuous human listening or publication approval.
"""
import datetime as dt
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

os.environ['HF_HUB_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['OMP_NUM_THREADS']='3'
import numpy as np
from faster_whisper import WhisperModel

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'private/postproduction/audio-scans'
VERSION='draft-all-intervals-turbo-int8-v1'


def atomic(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2),encoding='utf-8');tmp.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missing-only',action='store_true',help='Only decode intervals not contained in an identity-matched cached ASR report.')
    parser.add_argument('--timeline',type=Path,default=ROOT/'private/postproduction/draft-timeline.json')
    parser.add_argument('--status-name',default='batch-status.json')
    args=parser.parse_args()
    if Path(args.status_name).name!=args.status_name:raise ValueError('Status name must be a filename')
    status_path=OUT/args.status_name
    OUT.mkdir(parents=True,exist_ok=True)
    timeline=json.loads(args.timeline.read_text())
    clips=[dict(c,map=ch['map']) for ch in timeline['chapters'] for c in ch['clips']]
    draft_clips_total=len(clips)
    if args.missing_only:
        from align_draft_review_coverage import reports,covering
        cached=[(name,data) for name,data in reports(OUT) if data['key']['method']==VERSION
                and abs(data['audio_seconds_decoded']-(data['key']['end']-data['key']['start']))<=.15]
        clips=[clip for clip in clips if not covering(cached,clip,(ROOT/'recordings'/clip['file']).stat())]
    from project_paths import ffmpeg_path, WHISPER_MODEL
    ffmpeg=ffmpeg_path()
    modelpath=WHISPER_MODEL
    if not ffmpeg.is_file() or not modelpath.is_dir():raise RuntimeError('Existing offline dependencies unavailable; no download permitted')
    status={'pid':os.getpid(),'started_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'status':'loading_existing_offline_model','clips_total':len(clips),'clips_done':0,'audio_seconds_checked':0,
        'draft_clips_total':draft_clips_total,'scope':'Previously uncovered intervals only' if args.missing_only else 'All exact draft intervals',
        'method':VERSION,'full_listening_review':'pending','ready_to_publish':False}
    atomic(status_path,status);print(json.dumps(status),flush=True)
    if not clips:
        status['status']='no_uncovered_audio_intervals';atomic(status_path,status);return
    model=WhisperModel(str(modelpath),device='cpu',compute_type='int8',cpu_threads=3,local_files_only=True)
    for clip in clips:
        source=ROOT/'recordings'/clip['file'];st=source.stat()
        key={'file':clip['file'],'source_bytes':st.st_size,'source_mtime_ns':st.st_mtime_ns,
            'start':clip['source_start'],'end':clip['source_end'],'method':VERSION}
        ident=hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()[:24];dest=OUT/(ident+'.json')
        if dest.exists():
            result=json.loads(dest.read_text())
            if result.get('key')!=key:raise RuntimeError('ASR cache identity mismatch')
        else:
            duration=clip['source_end']-clip['source_start'];started=time.monotonic()
            command=[str(ffmpeg),'-hide_banner','-loglevel','error','-threads','1','-ss',str(clip['source_start']),
                '-i',str(source),'-t',str(duration),'-map','0:a:0','-vn','-ac','1','-ar','16000','-f','s16le','pipe:1']
            pcm=subprocess.run(command,capture_output=True,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
            audio=np.frombuffer(pcm,dtype=np.int16).astype(np.float32)/32768.
            if abs(len(audio)/16000-duration)>.15:raise RuntimeError('Selected audio interval was not fully decoded')
            segments,info=model.transcribe(audio,language='en',beam_size=3,vad_filter=True,condition_on_previous_text=False)
            rows=[{'start':s.start,'end':s.end,'text':s.text,'no_speech_prob':s.no_speech_prob} for s in segments]
            result={'key':key,'map':clip['map'],'audio_seconds_decoded':len(audio)/16000,'segments':rows,
                'coverage':'Full selected interval decoded and supplied to local ASR with VAD; no sampled audio intervals.',
                'limitation':'ASR/VAD can miss or misrecognize speech; not continuous human listening.',
                'transcript_review':'pending','elapsed_seconds':round(time.monotonic()-started,2)}
            atomic(dest,result)
        status.update(status='running',clips_done=status['clips_done']+1,
            audio_seconds_checked=status['audio_seconds_checked']+result['audio_seconds_decoded'],
            last_file=clip['file'],last_report=dest.name,updated_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        atomic(status_path,status)
        print(json.dumps({k:status[k] for k in ('clips_done','clips_total','audio_seconds_checked','last_report')}),flush=True)
    status['status']='selected_audio_asr_complete' if args.missing_only else 'draft_audio_asr_complete';atomic(status_path,status)


if __name__=='__main__':main()
