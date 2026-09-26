"""Read-only recording inventory and candidate run coverage for private editing.

Metadata/run-time overlap is a candidate association, never a privacy review or
proof that a whole recording is gameplay. No source media are changed/deleted.
"""
import datetime as dt
import json
import re
from pathlib import Path
import av

ROOT = Path(__file__).resolve().parent


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def inventory():
    log_anchors = {}
    pattern = re.compile(r"^(\d\d:\d\d:\d\d\.\d+): .*Writing file '([^']+\.mkv)'")
    for log in sorted((ROOT / 'tools/obs-portable/config/obs-studio/logs').glob('*.txt')):
        with log.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                match = pattern.search(line)
                if not match:
                    continue
                filename = Path(match[2]).name
                try:
                    named = dt.datetime.strptime(filename, 'L4D2-%Y-%m-%d-%H-%M-%S.mkv')
                    actual = dt.datetime.combine(named.date(), dt.time.fromisoformat(match[1]))
                    candidates = [actual + dt.timedelta(days=i) for i in (-1, 0, 1)]
                    actual = min(candidates, key=lambda x: abs((x - named).total_seconds()))
                    if abs((actual - named).total_seconds()) > 3:
                        continue
                    log_anchors[filename] = actual.astimezone(dt.timezone.utc).isoformat()
                except ValueError:
                    continue
    runs = []
    for name in ('run-events.jsonl', 'agent-runs.jsonl'):
        with (ROOT / name).open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                if row.get('started_utc') and row.get('finished_utc'):
                    runs.append(row)
    progress = json.loads((ROOT / 'campaign-progress.json').read_text())
    winners = {row['winning_round'] for row in progress['campaigns'].values()
               if row.get('completed') and row.get('winning_round')}
    result = []
    for path in sorted((ROOT / 'recordings').glob('*.mkv')):
        before = path.stat()
        row = {'file': path.name, 'bytes': before.st_size,
               'privacy_review': 'pending', 'edit_selection': 'pending'}
        try:
            with av.open(str(path)) as container:
                duration = container.duration / av.time_base if container.duration else None
                row['duration_seconds'] = duration
                row['streams'] = [
                    {'type': s.type, 'codec': s.codec_context.name,
                     **({'width': s.width, 'height': s.height,
                         'fps': str(s.average_rate)} if s.type == 'video' else {}),
                     **({'sample_rate': s.codec_context.sample_rate,
                         'channels': s.codec_context.channels} if s.type == 'audio' else {})}
                    for s in container.streams if s.type in ('video', 'audio')]
                created = container.metadata.get('creation_time')
                row['creation_time'] = created
                if not created:
                    created = log_anchors.get(path.name)
                    row['obs_log_start_utc'] = created
                    row['clock_association_uncertainty_seconds'] = 2 if created else None
                row['metadata_only'] = True
            after = path.stat()
            row['stable_at_inspection'] = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
            related = []
            for run in runs:
                final_path = (run.get('recording_finalization') or {}).get('path')
                exact = bool(final_path and Path(final_path).name == path.name)
                overlap = 0
                if created and duration:
                    start = stamp(created)
                    finish = start + dt.timedelta(seconds=duration)
                    overlap = max(0, (min(finish, stamp(run['finished_utc'])) -
                                      max(start, stamp(run['started_utc']))).total_seconds())
                if exact or overlap > 0:
                    related.append({
                        'started_utc': run['started_utc'], 'finished_utc': run['finished_utc'],
                        'map': run.get('map'), 'round_id': run.get('round_id'),
                        'start_game_t': run.get('start_game_t'), 'end_game_t': run.get('end_game_t', run.get('game_t')),
                        'exact_finalization_path': exact, 'clock_overlap_seconds': round(overlap, 3),
                        'verified_winning_finale_round': run.get('round_id') in winners,
                        'status': run.get('status'), 'reason': run.get('reason')})
            row['candidate_runs'] = related
            row['association_review'] = 'pending'
        except (av.error.FFmpegError, ValueError, OSError) as exc:
            row['inspection_error'] = f'{type(exc).__name__}: {exc}'
        result.append(row)
    out = ROOT / 'private' / 'postproduction' / 'footage-inventory.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'recordings': result, 'recording_count': len(result),
              'total_bytes': sum(r['bytes'] for r in result),
              'known_duration_seconds': sum(r.get('duration_seconds') or 0 for r in result),
              'all_visual_audio_review_complete': False,
              'note': 'Private metadata inventory only. Preserve every original. Review exact selected intervals and the actual final export before publication.'}
    temporary = out.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary.replace(out)
    print(json.dumps({'recording_count': len(result), 'known_hours': report['known_duration_seconds'] / 3600,
                      'gib': report['total_bytes'] / 1024**3,
                      'errors': sum('inspection_error' in r for r in result),
                      'unassociated': sum(not r.get('candidate_runs') for r in result),
                      'privacy_review': 'pending'}))


if __name__ == '__main__':
    inventory()
