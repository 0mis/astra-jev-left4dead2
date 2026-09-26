"""Map existing private review evidence onto a narrower edit without rescanning.

Superset ASR preserves decoding coverage, not caption accuracy or listening
review. OCR remains sparse message-area sampling even when an interval has a
report. Nothing produced here authorizes publication.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
POST = ROOT / 'private/postproduction'


def reports(folder):
    result = []
    for path in sorted(folder.glob('*.json')):
        if not re.fullmatch(r'[0-9a-f]{24}', path.stem):
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        key = data['key']
        identity = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
        if identity != path.stem:
            raise RuntimeError('Cached report identity mismatch: ' + path.name)
        result.append((path.name, data))
    return result


def covering(cached, clip, stat):
    choices = []
    for name, data in cached:
        key = data['key']
        if (key['file'] == clip['file'] and key['source_bytes'] == stat.st_size
                and key['source_mtime_ns'] == stat.st_mtime_ns
                and key['start'] <= clip['source_start'] + .001
                and key['end'] >= clip['source_end'] - .001):
            choices.append((key['end'] - key['start'], name, data))
    return min(choices, key=lambda item: item[:2])[1:] if choices else None


def main():
    timeline_bytes = (POST / 'draft-timeline.json').read_bytes()
    timeline = json.loads(timeline_bytes)
    audio = reports(POST / 'audio-scans')
    ocr = reports(POST / 'message-ocr')
    aliases = json.loads((POST / 'privacy-flags/private-keywords.json').read_text())['account_aliases']
    aliases = [re.sub('[^a-z0-9]', '', item.lower()) for item in aliases]
    keywords_hash = hashlib.sha256(json.dumps(aliases).encode()).hexdigest()
    ocr = [(name, data) for name, data in ocr
           if data['key']['keywords_sha256'] == keywords_hash]
    rows, fragments, retained_flags = [], [], []
    audio_seconds = 0.0
    for chapter in timeline['chapters']:
        for clip in chapter['clips']:
            stat = (ROOT / 'recordings' / clip['file']).stat()
            start, end = clip['source_start'], clip['source_end']
            row = dict(map=chapter['map'], file=clip['file'],
                       source_seconds=[start, end],
                       timeline_seconds=[clip['timeline_start'], clip['timeline_end']],
                       audio_evidence=None, message_sample_evidence=None)
            match = covering(audio, clip, stat)
            if match:
                name, report = match
                key = report['key']
                if abs(report['audio_seconds_decoded'] - (key['end'] - key['start'])) > .15:
                    raise RuntimeError('Incomplete decoded interval: ' + name)
                row['audio_evidence'] = dict(report=name, original_source_seconds=[key['start'], key['end']],
                                             retained_audio_seconds=end-start)
                audio_seconds += end-start
                for index, segment in enumerate(report['segments']):
                    left = key['start'] + max(0.0, segment['start'])
                    right = key['start'] + min(key['end']-key['start'], segment['end'])
                    a, b = max(start, left), min(end, right)
                    if b <= a:
                        continue
                    fragments.append(dict(map=chapter['map'], file=clip['file'], report=name,
                        segment=index, source_seconds=[a, b],
                        timeline_seconds=[clip['timeline_start']+a-start, clip['timeline_start']+b-start],
                        text=segment['text'].strip(),
                        text_spans_an_edit_boundary=left < start-.001 or right > end+.001,
                        original_asr_span_seconds=right-left,
                        publishable_caption=False))
            match = covering(ocr, clip, stat)
            if match:
                name, report = match
                samples = [sample for sample in report['rows'] if start <= sample['source_second'] < end]
                row['message_sample_evidence'] = dict(report=name, samples_inside=len(samples),
                    coverage='Existing one-second samples in two message areas; new cut boundaries may not have a matching sample.')
                for sample in samples:
                    normalized = re.sub('[^a-z0-9]', '', ' '.join(sample['texts']).lower())
                    if (sample['account_alias_candidate'] or sample['other_private_text_candidate']
                            or any(alias in normalized for alias in aliases)):
                        retained_flags.append(dict(map=chapter['map'], file=clip['file'],
                            report=name, source_second=sample['source_second'],
                            flag_image=sample.get('flag_image'), review='pending'))
            rows.append(row)
    result = dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        timeline_sha256=hashlib.sha256(timeline_bytes).hexdigest(),
        clips_total=len(rows), audio_clips_covered=sum(bool(row['audio_evidence']) for row in rows),
        retained_audio_seconds_covered=round(audio_seconds, 3),
        message_sample_clips_covered=sum(bool(row['message_sample_evidence']) for row in rows),
        message_sample_clips_pending=sum(not row['message_sample_evidence'] for row in rows),
        retained_message_flags=retained_flags, clips=rows, candidate_asr_fragments=fragments,
        caption_alignment='Private segment intersections only. Text spanning a cut may describe omitted audio; no word timing inferred.',
        full_visual_review='pending', full_listening_review='pending', final_export_review='pending',
        ready_to_publish=False)
    (POST / 'draft-review-coverage.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('clips_total', 'audio_clips_covered',
        'retained_audio_seconds_covered', 'message_sample_clips_covered', 'message_sample_clips_pending')}))
    print(json.dumps(dict(retained_message_flags=len(retained_flags),
        candidate_asr_fragments=len(fragments), cross_cut_text_fragments=sum(f['text_spans_an_edit_boundary'] for f in fragments))))


if __name__ == '__main__':
    main()
