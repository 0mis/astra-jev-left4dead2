"""Measure logged active game time without substituting calendar duration.

This is a lower bound: early records and short manual recovery/transition
captures can lack both clock endpoints. It does not infer their duration.
"""
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    rows = []
    for filename in ('run-events.jsonl', 'agent-runs.jsonl'):
        with (ROOT / filename).open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                if row.get('started_utc') and row.get('finished_utc'):
                    rows.append(row)
    rows.sort(key=lambda row: row['started_utc'])
    groups = defaultdict(list)
    epoch = defaultdict(int)
    previous_end = {}
    resets = []
    missing = []
    for row in rows:
        mapname = row.get('map')
        begin = row.get('start_game_t')
        end = row.get('end_game_t')
        endpoint = 'end_game_t'
        if end is None:
            end = row.get('game_t')
            endpoint = 'last_logged_game_t'
        if not mapname or not isinstance(begin, (int, float)) or not isinstance(end, (int, float)) or end < begin:
            missing.append({'map': mapname, 'started_utc': row['started_utc'],
                            'reason': 'Missing or inconsistent game-clock interval'})
            continue
        if mapname in previous_end and begin < previous_end[mapname] - 3:
            epoch[mapname] += 1
            resets.append({'map': mapname, 'started_utc': row['started_utc'],
                           'prior_end': previous_end[mapname], 'new_start': begin})
        previous_end[mapname] = end
        groups[(mapname, epoch[mapname])].append((begin, end, endpoint))
    measured = defaultdict(float)
    coverage = []
    for (mapname, clock_epoch), intervals in groups.items():
        merged = []
        for begin, end, _ in sorted(intervals):
            if merged and begin <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([begin, end])
        seconds = sum(end - begin for begin, end in merged)
        measured[mapname] += seconds
        coverage.append({'map': mapname, 'clock_epoch': clock_epoch,
                         'measured_seconds': seconds, 'intervals': merged,
                         'last_logged_endpoints': sum(kind == 'last_logged_game_t' for _, _, kind in intervals)})
    report = {'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'measured_controller_seconds': sum(measured.values()),
              'measured_controller_hours': sum(measured.values()) / 3600,
              'seconds_by_map': dict(measured), 'coverage': coverage,
              'clock_resets': resets, 'missing_intervals': missing,
              'is_lower_bound': True,
              'limitation': 'Known finalized controller intervals only, including retries. Excludes unlogged recovery, transition and early gameplay. Paused wall time is not added. Rerun after the last recording and controller finish. This is neither raw recording duration nor final edited runtime.',
              'public_headline_approved': False}
    out = ROOT / 'private/postproduction/controller-timing-audit.json'
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('measured_controller_hours', 'is_lower_bound', 'public_headline_approved')}))
    print(json.dumps({'missing_intervals': len(missing), 'clock_resets': len(resets)}))


if __name__ == '__main__':
    main()
