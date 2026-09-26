"""Private, local checks for a short publication clip; no raw media uploads.

Contact sheets and OCR are explicit samples. A successful scan is not a claim
that a human watched/listened to every frame, or that OCR/ASR cannot miss data.
"""
import argparse
import hashlib
import json
import os
import re
import time
import ctypes
from pathlib import Path

os.environ['HF_HUB_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
import av
from PIL import Image,ImageDraw


def main():
    p=argparse.ArgumentParser();p.add_argument('video',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    from project_paths import OCR_MODELS, WHISPER_MODEL
    from rapidocr import RapidOCR,OCRVersion,ModelType
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
    det=OCR_MODELS/'ch_PP-OCRv4_det_mobile.onnx'
    rec=OCR_MODELS/'ch_PP-OCRv4_rec_mobile.onnx'
    if not det.is_file() or not rec.is_file():
        raise RuntimeError('Existing offline OCR weights missing; no download allowed')
    ocr=RapidOCR(params={'Global.model_root_dir':str(OCR_MODELS),'Global.log_level':'warning','Global.use_cls':False,
        'Det.ocr_version':OCRVersion.PPOCRV4,'Rec.ocr_version':OCRVersion.PPOCRV4,
        'Det.model_type':ModelType.MOBILE,'Rec.model_type':ModelType.MOBILE,
        'Det.model_path':str(det),'Rec.model_path':str(rec),
        'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1})
    frames=0;first=None;last=0;next_sample=0;rows=[];images=[]
    pattern=re.compile(r'@|[A-Z]:[\\/]|\\Users\\|gmail|outlook|notifications|Steam Community|Shift\+Tab|\b\d{3}[- .]\d{3}[- .]\d{4}\b|\bPAUSED\b',re.I)
    with av.open(str(a.video)) as c:
        v=c.streams.video[0];v.codec_context.thread_count=2
        for frame in c.decode(v):
            frames+=1
            if frame.time is None:raise RuntimeError('Missing frame time')
            if first is None:first=frame.time
            last=frame.time-first
            if last+.001<next_sample:continue
            next_sample+=1
            result=ocr(frame.to_ndarray(format='bgr24'));texts=list(result.txts or [])
            rows.append({'time':round(last,3),'texts':texts,'flagged':bool(pattern.search('\n'.join(texts)))})
            im=frame.to_image();im.thumbnail((384,216));images.append((last,im))
            if len(rows)%30==0:print(json.dumps({'ocr_seconds_reviewed':last,'frames_decoded':frames}),flush=True)
    for page in range((len(images)+29)//30):
        subset=images[page*30:(page+1)*30];sheet=Image.new('RGB',(1152,2400),'#151515');d=ImageDraw.Draw(sheet)
        for n,(seconds,im) in enumerate(subset):
            x=(n%3)*384;y=(n//3)*240;sheet.paste(im,(x,y+24));d.text((x+8,y+5),f'{seconds:06.2f} sec',fill='white')
        sheet.save(a.out/f'contact-{page+1:02}.jpg',quality=92)
    (a.out/'ocr.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    from faster_whisper import WhisperModel
    model_path=WHISPER_MODEL
    model=WhisperModel(str(model_path),device='cpu',compute_type='int8',cpu_threads=3,local_files_only=True)
    segments,info=model.transcribe(str(a.video),language='en',beam_size=3,vad_filter=True,condition_on_previous_text=False)
    transcript=[{'start':s.start,'end':s.end,'text':s.text} for s in segments]
    (a.out/'transcript.json').write_text(json.dumps(transcript,indent=2),encoding='utf-8')
    report={'source':str(a.video),'sha256':hashlib.sha256(a.video.read_bytes()).hexdigest(),'frames_decoded':frames,
        'last_video_second':last,'ocr_interval_seconds':1,'ocr_samples':len(rows),'ocr_flagged':[r for r in rows if r['flagged']],
        'audio_duration':info.duration,'transcript_segments':len(transcript),'no_new_models_downloaded':True,
        'review_limitations':'All video frames decoded; visual/OCR samples at one second. ASR can omit or misrecognize speech. Not a continuous human audiovisual review.',
        'visual_contact_review':'pending','transcript_review':'pending'}
    (a.out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
