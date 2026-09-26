"""Private one-second OCR samples of game message areas, not full review.

Uses existing offline OCR weights. Originals are read only. Positive matches
are review candidates; missed text and short appearances remain possible.
"""
import ctypes
import argparse
import datetime as dt
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
import av
import cv2
import numpy as np
from rapidocr import RapidOCR, OCRVersion, ModelType

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'private/postproduction/message-ocr'
METHOD = 'message-areas-one-second-mobile-v1'


def atomic(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_timeline = ROOT / 'private/postproduction/draft-timeline.json'
    parser.add_argument('--timeline', type=Path, default=default_timeline)
    parser.add_argument('--status-name', default='batch-status.json')
    args = parser.parse_args()
    if Path(args.status_name).name != args.status_name or not args.status_name.endswith('.json'):
        parser.error('Status name must be one JSON filename')
    if args.timeline.resolve() != default_timeline.resolve() and args.status_name == 'batch-status.json':
        parser.error('A partial selection needs its own status filename')
    status_path = OUT / args.status_name
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'flags').mkdir(exist_ok=True)
    timeline_bytes = args.timeline.read_bytes()
    timeline = json.loads(timeline_bytes)
    aliases = json.loads((ROOT / 'private/postproduction/privacy-flags/private-keywords.json').read_text())['account_aliases']
    aliases = [re.sub('[^a-z0-9]', '', word.lower()) for word in aliases]
    from project_paths import OCR_MODELS
    models = OCR_MODELS
    det = models / 'ch_PP-OCRv4_det_mobile.onnx'
    rec = models / 'ch_PP-OCRv4_rec_mobile.onnx'
    if not det.is_file() or not rec.is_file():
        raise RuntimeError('Existing OCR weights missing; no download allowed')
    weights = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (det, rec)]
    clips = [dict(c, map=ch['map']) for ch in timeline['chapters'] for c in ch['clips']]
    status = dict(pid=os.getpid(), started_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                  status='loading_existing_offline_weights', method=METHOD,
                  timeline_sha256=hashlib.sha256(timeline_bytes).hexdigest(),
                  clips_total=len(clips), clips_done=0, samples_checked=0, flagged_samples=0,
                  full_visual_review='pending', ready_to_publish=False)
    atomic(status_path, status)
    print(json.dumps(status), flush=True)
    ocr = RapidOCR(params={
        'Global.model_root_dir': str(models), 'Global.log_level': 'warning', 'Global.use_cls': False,
        'Det.ocr_version': OCRVersion.PPOCRV4, 'Rec.ocr_version': OCRVersion.PPOCRV4,
        'Det.model_type': ModelType.MOBILE, 'Rec.model_type': ModelType.MOBILE,
        'Det.limit_type': 'max', 'Det.limit_side_len': 1280,
        'Det.model_path': str(det), 'Rec.model_path': str(rec),
        'EngineConfig.onnxruntime.intra_op_num_threads': 2,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
    })
    private_pattern = re.compile(r'@|[A-Z]:[\\/]|\\Users\\|\.codex|gmail|outlook|Steam Community|Shift\+Tab', re.I)
    for clip in clips:
        if shutil.disk_usage(ROOT).free < 6 * 2**30:
            raise RuntimeError('Private review stopped before approaching the storage floor')
        source = ROOT / 'recordings' / clip['file']
        st = source.stat()
        key = dict(file=clip['file'], source_bytes=st.st_size, source_mtime_ns=st.st_mtime_ns,
                   start=clip['source_start'], end=clip['source_end'], method=METHOD,
                   weights=weights, keywords_sha256=hashlib.sha256(json.dumps(aliases).encode()).hexdigest())
        ident = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
        dest = OUT / (ident + '.json')
        if dest.is_file():
            result = json.loads(dest.read_text())
            if result['key'] != key:
                raise RuntimeError('Cached OCR identity mismatch')
        else:
            start, end = clip['source_start'], clip['source_end']
            targets = {round(start + .05, 6), round(end - .05, 6)}
            moment = start + 1.05
            while moment < end - .05:
                targets.add(round(moment, 6)); moment += 1
            targets = sorted(targets)
            rows = []; target_index = 0; decoded = 0; last_flag = -100
            began = time.monotonic()
            with av.open(str(source)) as container:
                stream = container.streams.video[0]
                stream.codec_context.thread_count = 1
                container.seek(int(start / stream.time_base), stream=stream, backward=True)
                for frame in container.decode(stream):
                    if frame.time is None:
                        raise RuntimeError('Video frame timestamp missing')
                    if frame.time < start - .001:
                        continue
                    if frame.time >= end:
                        break
                    decoded += 1
                    if target_index >= len(targets) or frame.time + .02 < targets[target_index]:
                        continue
                    while target_index < len(targets) and targets[target_index] <= frame.time + .02:
                        target_index += 1
                    im = frame.to_ndarray(format='bgr24')
                    if im.shape[:2] != (720, 1280):
                        raise RuntimeError('Unexpected source dimensions')
                    composite = np.vstack([im[225:365, :760], np.zeros((20, 760, 3), np.uint8), im[400:565, :760]])
                    result_ocr = ocr(cv2.resize(composite, None, fx=1.5, fy=1.5))
                    texts = list(result_ocr.txts or [])
                    words = re.findall('[a-z0-9]+', ' '.join(texts).lower())
                    alias_hit = any(4 <= len(w) <= 10 and SequenceMatcher(None, w, alias).ratio() >= .70
                                    for w in words for alias in aliases)
                    other_hit = bool(private_pattern.search('\n'.join(texts)))
                    row = dict(source_second=round(frame.time, 6), texts=texts,
                               account_alias_candidate=alias_hit, other_private_text_candidate=other_hit)
                    if alias_hit or other_hit:
                        if frame.time - last_flag > 1.5:
                            name = ident + '-' + str(len(rows)).zfill(5) + '.png'
                            cv2.imwrite(str(OUT / 'flags' / name), im)
                            row['flag_image'] = 'flags/' + name
                        last_flag = frame.time
                    rows.append(row)
                    if len(rows) % 30 == 0:
                        status.update(status='running', current_file=source.name,
                                      current_source_second=frame.time, current_clip_samples=len(rows),
                                      updated_utc=dt.datetime.now(dt.timezone.utc).isoformat())
                        atomic(status_path, status)
                        print(json.dumps({k: status[k] for k in ('clips_done', 'clips_total', 'current_clip_samples', 'current_source_second')}), flush=True)
            if target_index != len(targets):
                raise RuntimeError('Not every planned OCR sample was reached')
            result = dict(key=key, map=clip['map'], rows=rows, frames_decoded=decoded,
                          elapsed_seconds=round(time.monotonic()-began, 3),
                          coverage='One-second samples plus interval boundaries in two known game-message areas.',
                          limitation='Not every-frame OCR, not a full-frame visual review, and no guarantee against missed text.',
                          flag_review='pending', ready_to_publish=False)
            atomic(dest, result)
        status.update(status='running', clips_done=status['clips_done']+1,
                      samples_checked=status['samples_checked']+len(result['rows']),
                      flagged_samples=status['flagged_samples']+sum(r['account_alias_candidate'] or r['other_private_text_candidate'] for r in result['rows']),
                      last_report=dest.name, updated_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        atomic(status_path, status)
    status['status'] = 'draft_message_sample_scan_complete'
    atomic(status_path, status)
    print(json.dumps(status), flush=True)


if __name__ == '__main__':
    main()
