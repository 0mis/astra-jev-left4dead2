"""Assemble private chapter edits from exact reviewed source selections.

Planning is read-only except for the private plan. Rendering requires every
original campaign's verified completion, preserves raw recordings, and never
publishes or treats an encoded file as having passed audiovisual review.
"""
import argparse
import ctypes
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import av

ROOT = Path(__file__).resolve().parent
POST = ROOT / 'private/postproduction'
from project_paths import ffmpeg_path
FFMPEG = ffmpeg_path()
FLOOR = 5 * 1024 ** 3


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(path)


def probe(path):
    with av.open(str(path)) as container:
        video = container.streams.video
        audio = container.streams.audio
        if len(video) != 1 or len(audio) != 1:
            raise ValueError('Expected one video and one audio stream')
        if (video[0].width, video[0].height) != (1280, 720):
            raise ValueError('Unexpected chapter dimensions')
        return {'duration_seconds': container.duration / av.time_base,
                'width': video[0].width, 'height': video[0].height,
                'video_codec': video[0].codec_context.name,
                'audio_codec': audio[0].codec_context.name,
                'fps': str(video[0].average_rate),
                'audio_channels': audio[0].codec_context.channels,
                'audio_sample_rate': audio[0].codec_context.sample_rate}


def plan():
    data = json.loads((POST / 'draft-timeline.json').read_text())
    chapters = []
    sources = {}
    for chapter in data['chapters']:
        if not re.fullmatch(r'c[1-5]m[1-5]_[a-z_]+', chapter['map']):
            raise ValueError('Invalid chapter identifier')
        clips = []
        for clip in chapter['clips']:
            filename = clip['file']
            if Path(filename).name != filename:
                raise ValueError('Source must be a filename in recordings')
            source = ROOT / 'recordings' / filename
            stat = source.stat()
            sources[filename] = {'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
            start, end = clip['source_start'], clip['source_end']
            if not 0 <= start < end:
                raise ValueError('Invalid source interval')
            clips.append({'file': filename, 'start': start, 'end': end})
        if not clips:
            raise ValueError('Empty chapter')
        chapters.append({'map': chapter['map'], 'campaign': chapter['campaign'],
                         'clips': clips, 'selected_seconds': sum(c['end']-c['start'] for c in clips)})
    identity = {'chapters': chapters, 'sources': sources,
                'settings': {'width': 1280, 'height': 720, 'fps': 30,
                             'video_kbps': 2500, 'audio_kbps': 128,
                             'codec': 'h264_nvenc', 'preset': 'p5'}}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    result = {'created_utc': stamp(), 'identity_sha256': digest, **identity,
              'scope': 'Private story chapters only; editorial cards and final export review remain separate.',
              'ready_to_publish': False, 'full_export_review': 'pending',
              'timeline_sha256': hashlib.sha256((POST/'draft-timeline.json').read_bytes()).hexdigest()}
    save(POST / 'chapter-render-plan.json', result)
    return result


def command(chapter, target):
    args = [str(FFMPEG), '-hide_banner', '-nostdin', '-y', '-loglevel', 'warning']
    filters = []
    for i, clip in enumerate(chapter['clips']):
        duration = clip['end'] - clip['start']
        args += ['-threads', '1', '-ss', f"{clip['start']:.6f}", '-t', f'{duration:.6f}',
                 '-i', str(ROOT / 'recordings' / clip['file'])]
        filters += [f'[{i}:v]trim=duration={duration:.6f},setpts=PTS-STARTPTS,fps=30,format=yuv420p[v{i}]',
                    f'[{i}:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,aresample=48000[a{i}]']
    labels = ''.join(f'[v{i}][a{i}]' for i in range(len(chapter['clips'])))
    filters.append(f'{labels}concat=n={len(chapter["clips"])}:v=1:a=1[v][a]')
    args += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
             '-map', '[v]', '-map', '[a]', '-map_metadata', '-1', '-map_chapters', '-1',
             '-c:v', 'h264_nvenc', '-preset', 'p5', '-b:v', '2500k',
             '-maxrate', '3500k', '-bufsize', '7000k', '-g', '60', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-b:a', '128k', '-ar', '48000', '-ac', '2',
             '-movflags', '+faststart', str(target)]
    return args


def render(data, only=None):
    if only and only not in {c['map'] for c in data['chapters']}:
        raise ValueError('Unknown selected chapter')
    progress = json.loads((ROOT / 'campaign-progress.json').read_text())['campaigns']
    required = ('Dead Center', 'Dark Carnival', 'Swamp Fever', 'Hard Rain', 'The Parish')
    if not all(progress.get(name, {}).get('completed') for name in required):
        raise RuntimeError('Campaign gameplay remains incomplete; do not compete with it for rendering resources')
    if len(data['chapters']) != 23:
        raise RuntimeError('The full story selection must contain all23chapters before rendering')
    for filename, expected in data['sources'].items():
        stat = (ROOT / 'recordings' / filename).stat()
        if (stat.st_size, stat.st_mtime_ns) != (expected['bytes'], expected['mtime_ns']):
            raise RuntimeError('Selected source changed')
    # Reserve room for both chapter files and a final assembled copy. Keep raw
    # footage intact and the user's5GiB free-space floor throughout the job.
    expected_bytes = sum(c['selected_seconds'] for c in data['chapters']) * 340000
    folder = POST / 'chapter-renders' / data['identity_sha256'][:16]
    folder.mkdir(parents=True, exist_ok=True)
    existing_bytes = sum(p.stat().st_size for p in folder.glob('*.mp4')
                         if not p.name.endswith('.partial.mp4') and p.with_suffix('.json').is_file())
    if shutil.disk_usage(ROOT).free < FLOOR + max(0, expected_bytes-existing_bytes) + expected_bytes:
        raise RuntimeError('Insufficient free space for chapters, final assembly and5GiB reserve')
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    state = {'started_utc': stamp(), 'status': 'running', 'ready_to_publish': False,
             'identity_sha256': data['identity_sha256'], 'chapters': []}
    status_path = folder / 'status.json'
    save(status_path, state)
    for chapter in data['chapters']:
        if only and chapter['map'] != only:
            continue
        target = folder / (chapter['map'] + '.mp4')
        receipt = target.with_suffix('.json')
        if target.is_file() and receipt.is_file():
            cached = json.loads(receipt.read_text())
            if (cached.get('bytes') == target.stat().st_size
                and cached.get('identity_sha256') == data['identity_sha256']
                and cached.get('sha256') == file_hash(target)):
                state['chapters'].append(cached)
                continue
        if shutil.disk_usage(ROOT).free < FLOOR + chapter['selected_seconds'] * 340000:
            raise RuntimeError('Free-space reserve would be crossed')
        partial = target.with_name(target.stem + '.partial.mp4')
        with target.with_suffix('.encode.log').open('w') as log:
            process = subprocess.Popen(command(chapter, partial), stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                while process.poll() is None:
                    if shutil.disk_usage(ROOT).free < FLOOR + 64 * 1024 ** 2:
                        raise RuntimeError('Stopped encoder to preserve the5GiB storage reserve')
                    time.sleep(.5)
                if process.returncode:
                    raise RuntimeError('Chapter encoder failed; inspect its private log')
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        facts = probe(partial)
        # Frame rounding may add at most one frame per selected interval;
        # allow AAC boundary padding too, but reject a truncated chapter.
        tolerance = len(chapter['clips']) * .08 + .3
        if abs(facts['duration_seconds'] - chapter['selected_seconds']) > tolerance:
            raise RuntimeError('Rendered duration differs from the selected source intervals')
        partial.replace(target)
        result = {'map': chapter['map'], 'identity_sha256': data['identity_sha256'],
                  'file': target.name, 'bytes': target.stat().st_size, **facts,
                  'sha256': file_hash(target),
                  'visual_review': 'pending', 'audio_review': 'pending', 'ready_to_publish': False}
        save(receipt, result)
        state['chapters'].append(result)
        state['updated_utc'] = stamp()
        save(status_path, state)
        print(json.dumps({'chapter_encoded': chapter['map'], 'seconds': facts['duration_seconds'],
                          'review_pending': True}), flush=True)
    state['status'] = 'selected_chapters_encoded_review_pending'
    state['finished_utc'] = stamp()
    save(status_path, state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--chapter')
    args = parser.parse_args()
    data = plan()
    print(json.dumps({'planned_chapters': len(data['chapters']),
                      'identity_sha256': data['identity_sha256'], 'ready_to_publish': False}))
    if args.render:
        render(data, args.chapter)
