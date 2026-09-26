"""Assemble identity-matched draft ASR for private review, not publication."""
import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'private/postproduction/audio-scans'


def main():
    timeline_path = ROOT / 'private/postproduction/draft-timeline.json'
    timeline_bytes = timeline_path.read_bytes()
    timeline = json.loads(timeline_bytes)
    method = json.loads((OUT / 'batch-status.json').read_text())['method']
    rows, missing, flags, lines = [], [], [], []
    checked, decoded = 0, 0.0
    for chapter in timeline['chapters']:
        lines.append('\n' + chapter['campaign'] + ' | ' + chapter['map'])
        for clip in chapter['clips']:
            source = ROOT / 'recordings' / clip['file']
            st = source.stat()
            key = dict(file=clip['file'], source_bytes=st.st_size,
                       source_mtime_ns=st.st_mtime_ns,
                       start=clip['source_start'], end=clip['source_end'], method=method)
            ident = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
            report = OUT / (ident + '.json')
            if not report.is_file():
                missing.append(dict(map=chapter['map'], key=key, report=report.name))
                continue
            result = json.loads(report.read_text())
            if result['key'] != key:
                raise RuntimeError('Source or interval identity mismatch')
            checked += 1
            decoded += result['audio_seconds_decoded']
            duration = clip['source_end'] - clip['source_start']
            if abs(result['audio_seconds_decoded'] - duration) > .15:
                raise RuntimeError('Incomplete decoded audio interval')
            for i, segment in enumerate(result['segments']):
                start = max(0.0, min(duration, segment['start']))
                end = max(start, min(duration, segment['end']))
                row = dict(map=chapter['map'], file=clip['file'], report=report.name,
                           segment=i, source_start=clip['source_start'] + start,
                           source_end=clip['source_start'] + end,
                           timeline_start=clip['timeline_start'] + start,
                           timeline_end=clip['timeline_start'] + end,
                           text=segment['text'].strip(),
                           no_speech_prob=segment['no_speech_prob'])
                rows.append(row)
                lines.append(f"{row['source_start']:.3f}-{row['source_end']:.3f} "
                             f"[{report.stem}:{i}] {row['text']}")
                if end - start > 15:
                    flags.append(dict(report=report.name, segment=i,
                                      reason='ASR span exceeds 15 seconds; timing unsuitable for direct captions'))
    data = dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                timeline_sha256=hashlib.sha256(timeline_bytes).hexdigest(),
                source_clips_checked=checked, audio_seconds_decoded=decoded,
                missing_reports=missing, segments=rows, caption_timing_flags=flags,
                transcript_text_review='pending', full_listening_review='pending',
                ready_to_publish=False,
                limitation='ASR and VAD may miss or misrecognize speech. Identity-matched '
                           'full-interval decoding is not continuous listening or publication approval.')
    (OUT / 'draft-transcript-index.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    (OUT / 'draft-transcript.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(dict(clips=checked, missing=len(missing), segments=len(rows),
                          words=sum(len(r['text'].split()) for r in rows),
                          decoded_seconds=decoded, long_timing_spans=len(flags))))


if __name__ == '__main__':
    main()
