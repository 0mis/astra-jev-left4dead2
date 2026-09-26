"""Render the private story edit directly, without a second full-size media copy.

Source cuts use FFmpeg's concat segment metadata and decoded-frame selection.
An encoded file is never publication approval or proof of audiovisual review.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import time

import av
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
POST = ROOT / 'private/postproduction'
OUT = POST / 'story-export'
from project_paths import ffmpeg_path
FFMPEG = ffmpeg_path()
FLOOR = 5 * 1024 ** 3
CARDS = [
    ('opening', 8, 'GPT-6 ASTRA + JEV', 'LEFT 4 DEAD 2',
     ['All five original campaigns | 23 chapters', 'Easy | private single player | bot teammates', 'The successful attempts, with pauses and waiting cut.']),
    ('team', 10, 'RETROSPECTIVE COMMENTARY', 'HOW THE TEAM WORKED',
     ['Jev chose tasks and combat targets.', 'The controller handled navigation, aiming and ordinary inputs.',
      'GPT-6 Astra planned, checked screenshots and repaired loops.', 'This was telemetry-assisted play, not a vision-only run.']),
    ('Dead Center', 8, 'RETROSPECTIVE COMMENTARY', '01 / DEAD CENTER',
     ['The fuel-delivery finale took 24 activated attempts.', 'There was also one interrupted run before activation.',
      'This edit shows the successful attempt.']),
    ('Dark Carnival', 8, 'RETROSPECTIVE COMMENTARY', '02 / DARK CARNIVAL',
     ['Fairgrounds took two attempts. The concert finale took one.', 'Our character was incapacitated when the escape counted.',
      'A verified win, with an untidy getaway.']),
    ('Swamp Fever', 8, 'RETROSPECTIVE COMMENTARY', '03 / SWAMP FEVER',
     ['The Swamp chapter and Plantation finale each took two attempts.', 'After the first plane-horde loss, the team regrouped before moving.',
      'The successful retries are the ones shown here.']),
    ('Hard Rain', 8, 'RETROSPECTIVE COMMENTARY', '04 / HARD RAIN',
     ['All five chapters were completed on their first attempts.', 'Elevator progress needed real platform and teammate checks.',
      'The edit preserves the trip out, the return and the escape.']),
    ('The Parish', 10, 'RETROSPECTIVE COMMENTARY', '05 / THE PARISH',
     ['Park took two attempts. Bridge took 13 runs:', '11 losses, one engine crash, then the winning escape.',
      'The winning Bridge round lasted 13m33s of game time.', 'Only the successful Bridge attempt appears in this edit.']),
    ('paranoid', 10, 'RETROSPECTIVE COMMENTARY', 'THE PARANOID PART',
     ['Is the recorder running? Are we repeating the same failed move?', 'Did the game actually confirm the escape?',
      'Capture checks, loop handoffs and engine win events', 'became part of the routine.']),
    ('result', 10, 'VERIFIED RESULT', 'FIVE CAMPAIGNS. ALL 23 CHAPTERS.',
     ['At least 13.8 hours of logged play, including retries.', '21.9 hours of raw capture, including pauses and setup.',
      'These are different measures, not an exact total completion time.', 'Jev usage ledger: $12.27 | Astra cost not calculated.']),
]


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 ** 2), b''):
            h.update(chunk)
    return h.hexdigest()


def make_card(index, card):
    name, seconds, kicker, title, lines = card
    folder = OUT / 'cards'
    folder.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (1280, 720), '#0d1516')
    d = ImageDraw.Draw(image)
    fonts = Path(os.environ['WINDIR']) / 'Fonts'
    font = lambda n, bold=False: ImageFont.truetype(str(fonts / ('arialbd.ttf' if bold else 'arial.ttf')), n)
    d.rectangle((76, 81, 1204, 86), fill='#bcf56b')
    d.text((76, 120), kicker, font=font(25, True), fill='#bcf56b')
    size = 54
    while d.textbbox((0, 0), title, font=font(size, True))[2] > 1128:
        size -= 1
    d.text((76, 190), title, font=font(size, True), fill='white')
    for j, line in enumerate(lines):
        if d.textbbox((0, 0), line, font=font(30))[2] > 1128:
            raise ValueError('Card text needs reflow: ' + line)
        d.text((76, 325 + j * 56), line, font=font(30), fill='#e4eae7')
    d.text((76, 638), 'ASTRA + JEV / LEFT 4 DEAD 2', font=font(19), fill='#9aa9a4')
    png = folder / f'{index:02}.png'
    target = folder / f'{index:02}.mkv'
    image.save(png)
    cmd = [str(FFMPEG), '-hide_banner', '-nostdin', '-y', '-loglevel', 'error',
           '-loop', '1', '-framerate', '30', '-i', str(png), '-f', 'lavfi',
           '-i', 'anullsrc=r=48000:cl=stereo', '-t', str(seconds),
           '-c:v', 'h264_nvenc', '-preset', 'p5', '-b:v', '2500k', '-g', '60',
           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
           '-ac', '2', '-map_metadata', '-1', str(target)]
    subprocess.run(cmd, capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return {'file': str(target.relative_to(ROOT)), 'source_start': 0.,
            'source_end': float(seconds), 'kind': 'retrospective_card', 'card': name}


def prepare():
    completed = json.loads((ROOT / 'main-campaign-completion.json').read_text())
    if completed['campaigns_completed'] != 5 or completed['chapters_completed'] != 23:
        raise RuntimeError('Verified original-campaign completion is required')
    timeline = json.loads((POST / 'draft-timeline.json').read_text())
    if len(timeline['chapters']) != 23 or timeline['pending_sources']:
        raise RuntimeError('Incomplete story timeline')
    if shutil.disk_usage(ROOT).free < FLOOR + 100 * 1024 ** 2:
        raise RuntimeError('Insufficient space even for editorial cards')
    assets = {card[0]: make_card(i, card) for i, card in enumerate(CARDS)}
    clips, chapters, clock = [], [], 0.

    def append(clip):
        nonlocal clock
        source = ROOT / clip['file']
        st = source.stat()
        duration = clip['source_end'] - clip['source_start']
        clips.append(dict(clip, timeline_start=clock, timeline_end=clock+duration,
                          source_bytes=st.st_size, source_mtime_ns=st.st_mtime_ns))
        clock += duration

    append(assets['opening'])
    append(assets['team'])
    campaign = None
    for chapter in timeline['chapters']:
        if chapter['campaign'] != campaign:
            campaign = chapter['campaign']
            append(assets[campaign])
        chapters.append({'map': chapter['map'], 'campaign': campaign, 'start_seconds': clock})
        for clip in chapter['clips']:
            append({'file': 'recordings/' + clip['file'], 'source_start': clip['source_start'],
                    'source_end': clip['source_end'], 'kind': 'gameplay', 'map': chapter['map']})
    append(assets['paranoid'])
    append(assets['result'])
    data = {'created_utc': stamp(), 'clips': clips, 'chapters': chapters,
            'duration_seconds': clock, 'timeline_sha256': sha(POST / 'draft-timeline.json'),
            'settings': {'video_kbps': 2500, 'maxrate_kbps': 3500, 'audio_kbps': 128, 'fps': 30},
            'ready_to_publish': False, 'review': 'pending', 'cards': CARDS,
            'method_reference': 'https://ffmpeg.org/ffmpeg-filters.html#select_002c-aselect'}
    data['identity_sha256'] = hashlib.sha256(json.dumps({'clips': clips, 'settings': data['settings']}, sort_keys=True).encode()).hexdigest()
    save(OUT / 'plan.json', data)
    print(json.dumps({'prepared': True, 'chapters': len(chapters), 'segments': len(clips),
                      'duration_seconds': clock, 'identity_sha256': data['identity_sha256']}), flush=True)
    return data


def render(data, smoke=False):
    lock = (OUT / 'render.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0'); lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    clips = data['clips']
    if smoke:
        # Exercise each source/cut while keeping the test small. Every card is
        # retained fully so card text can also be inspected.
        clips = [dict(c, source_end=min(c['source_end'], c['source_start'] + 0.8))
                 if c['kind'] == 'gameplay' else c for c in clips]
    duration = sum(c['source_end'] - c['source_start'] for c in clips)
    expected_max_bytes = duration * 460000 + 128 * 1024 ** 2
    if shutil.disk_usage(ROOT).free < FLOOR + expected_max_bytes:
        raise RuntimeError('Insufficient space for direct export and5GiB reserve')
    stem = 'smoke' if smoke else 'L4D2-Astra-Jev-Full-Story'
    target = OUT / (stem + '.mp4')
    receipt = OUT / (stem + '.json')
    if target.exists():
        raise RuntimeError('Output already exists; inspect its receipt instead of overwriting')
    lines = ['ffconcat version 1.0']
    for clip in clips:
        path = ROOT / clip['file']; st = path.stat()
        if (st.st_size, st.st_mtime_ns) != (clip['source_bytes'], clip['source_mtime_ns']):
            raise RuntimeError('Source identity changed')
        name = path.as_posix()
        if "'" in name or '\n' in name:
            raise ValueError('Unsupported file name in edit list')
        lines += [f"file '{name}'", f"inpoint {clip['source_start']:.6f}",
                  f"outpoint {clip['source_end']:.6f}",
                  f"duration {clip['source_end'] - clip['source_start']:.6f}"]
    listing = OUT / (stem + '.ffconcat')
    listing.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    frames = math.ceil(duration * 30 - 1e-6)
    cmd = [str(FFMPEG), '-hide_banner', '-nostdin', '-y', '-loglevel', 'warning',
           '-copyts', '-threads', '2', '-f', 'concat', '-safe', '0', '-segment_time_metadata', '1',
           '-i', str(listing), '-vf', f'select=concatdec_select,fps=30,trim=end_frame={frames},format=yuv420p',
           '-af', f'aselect=concatdec_select,aresample=48000:async=1:first_pts=0,apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}',
           '-map', '0:v:0', '-map', '0:a:0', '-map_metadata', '-1', '-map_chapters', '-1',
           '-c:v', 'h264_nvenc', '-preset', 'p5', '-b:v', '2500k', '-maxrate', '3500k',
           '-bufsize', '7000k', '-g', '60', '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
           '-ac', '2', '-movflags', '+faststart', '-progress', str(OUT / (stem + '.progress')),
           str(target)]
    state = {'status': 'encoding', 'started_utc': stamp(), 'pid': os.getpid(),
             'identity_sha256': data['identity_sha256'], 'file': target.name,
             'expected_duration_seconds': duration, 'ready_to_publish': False}
    with (OUT / (stem + '.encode.log')).open('w') as log:
        process = subprocess.Popen(cmd, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        state['encoder_pid'] = process.pid; save(receipt, state)
        try:
            while process.poll() is None:
                if shutil.disk_usage(ROOT).free < FLOOR + 64 * 1024 ** 2:
                    raise RuntimeError('Stopped encoder to protect5GiB reserve')
                time.sleep(.5)
            if process.returncode:
                raise RuntimeError('Export encoder failed; inspect private log')
        except BaseException as exc:
            state.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
            save(receipt, state)
            raise
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=10)
    with av.open(str(target)) as c:
        actual = c.duration / av.time_base
        facts = {'duration_seconds': actual, 'width': c.streams.video[0].width,
                 'height': c.streams.video[0].height, 'metadata': dict(c.metadata),
                 'audio_streams': len(c.streams.audio), 'video_streams': len(c.streams.video)}
    if abs(actual - duration) > .1:
        raise RuntimeError('Export duration differs from edit plan')
    state.update(status='encoded_review_pending', finished_utc=stamp(), **facts,
                 bytes=target.stat().st_size, sha256=sha(target), free_gib=shutil.disk_usage(ROOT).free/1024**3)
    save(receipt, state)
    print(json.dumps(state), flush=True)
    lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        data = prepare()
    else:
        data = json.loads((OUT/'plan.json').read_text())
    if args.smoke or args.render:
        render(data, smoke=args.smoke)
