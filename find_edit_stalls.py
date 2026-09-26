"""Find private editorial candidates from recorded ineffective movement.

No source or timeline mutation. Clock associations and telemetry are candidate
locators; inspect the actual footage before removing anything.
"""
from bisect import bisect_right
from collections import defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POST = ROOT / 'private/postproduction'


def stamp(text):
    return dt.datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()


def signature(row):
    metrics = row.get('metrics', {})
    return tuple([row.get('health')] + [metrics.get(k) for k in ('shots', 'uses', 'pours', 'heals', 'revives')])


def main():
    raw = (POST / 'draft-timeline.json').read_bytes()
    timeline = json.loads(raw)
    inventory = json.loads((POST / 'footage-inventory.json').read_text())
    media = {row['file']: row for row in inventory['recordings']}
    clips = []
    for chapter in timeline['chapters']:
        for row in chapter['clips']:
            anchor = media[row['file']].get('creation_time') or media[row['file']].get('obs_log_start_utc')
            if not anchor:
                continue
            absolute = stamp(anchor)
            clips.append(dict(row, map=chapter['map'], anchor=absolute,
                              utc_start=absolute+row['source_start'], utc_end=absolute+row['source_end']))
    clips.sort(key=lambda row: row['utc_start'])
    starts = [row['utc_start'] for row in clips]
    for previous, current in zip(clips, clips[1:]):
        if previous['utc_end'] > current['utc_start'] + .001:
            raise RuntimeError('Overlapping media association requires manual review')
    groups = defaultdict(list)
    total = 0
    with (ROOT / 'agent-actions.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            total += 1
            when = stamp(row['utc'])
            index = bisect_right(starts, when)-1
            if index >= 0 and when < clips[index]['utc_end']:
                groups[index].append(dict(row, source_second=when-clips[index]['anchor']))
    candidates = []
    for index, rows in sorted(groups.items()):
        clip = clips[index]
        batch = []

        def emit():
            if len(batch) < 2:
                return
            a, b = batch[0], batch[-1]
            duration = b['source_second']-a['source_second']
            if duration < 15:
                return
            candidates.append(dict(file=clip['file'], map=clip['map'],
                source_seconds=[round(a['source_second'], 3), round(b['source_second'], 3)],
                proposed_interior_cut=[round(a['source_second']+2, 3), round(b['source_second']-2, 3)],
                duration_seconds=round(duration, 3), game_seconds=[a['game_t'], b['game_t']],
                action_samples=len(batch), maximum_distance_from_start=round(max(math.dist(a['p'], item['p']) for item in batch), 2),
                health=a['health'], reason='Movement commanded within 45 game units, with unchanged logged health/shots/uses/pours/heals/revives.',
                actual_footage_review='pending', applied=False))

        for row in rows:
            action = row.get('action', {})
            moving = action.get('task') == 'route' and bool(set(action.get('keys', [])) & {'w', 'a', 's', 'd'})
            if not moving:
                emit(); batch = []; continue
            if batch:
                last, first = batch[-1], batch[0]
                wall_gap = row['source_second']-last['source_second']
                game_gap = row['game_t']-last['game_t']
                continuous = (0 < wall_gap <= 3 and game_gap > 0 and abs(wall_gap-game_gap) < .5)
                if not continuous or signature(row) != signature(first) or math.dist(row['p'], first['p']) > 45:
                    emit(); batch = []
            batch.append(row)
        emit()
    candidates.sort(key=lambda row: row['duration_seconds'], reverse=True)
    result = dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        timeline_sha256=hashlib.sha256(raw).hexdigest(), action_rows_read=total,
        candidates=candidates, ready_to_publish=False,
        limitation='Telemetry and OBS wall-clock anchors only locate candidates. No visual/audio review is established; no cuts applied.')
    (POST / 'stationary-movement-candidates.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(action_rows_read=total, candidates=len(candidates),
        candidate_seconds=round(sum(row['duration_seconds'] for row in candidates), 3), top=candidates[:8])))


if __name__ == '__main__':
    main()
