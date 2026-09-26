"""Draft private successful-attempt media candidates; never render or publish.

Clock associations are approximate. Confirm boundaries, pauses, missing helper
captures and privacy coverage against actual footage before any final export.
"""
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def build():
    progress = read(ROOT / 'campaign-progress.json')
    inventory = read(ROOT / 'private/postproduction/footage-inventory.json')
    hotel_rule_path=ROOT/'private/postproduction/hotel-selection-rule.json'
    hotel_rule=read(hotel_rule_path) if hotel_rule_path.exists() else None
    chapters = []
    for campaign, row in progress['campaigns'].items():
        for chapter in row['chapters']:
            mapname = chapter['map']
            evidence = ROOT / 'private' / (mapname.split('_')[0] + '-completion-state.json')
            state = read(evidence)
            winning_round = state.get('round_id') or chapter.get('winning_round')
            candidates = []
            for media in inventory['recordings']:
                for run in media.get('candidate_runs', []):
                    if run.get('map') != mapname:
                        continue
                    if winning_round and run.get('round_id') != winning_round:
                        continue
                    if hotel_rule and mapname==hotel_rule['map'] and media['file']<hotel_rule['source']:
                        continue
                    anchor = media.get('creation_time') or media.get('obs_log_start_utc')
                    duration = media.get('duration_seconds')
                    clip = None
                    if anchor and duration is not None:
                        begin = max(0, (stamp(run['started_utc']) - stamp(anchor)).total_seconds())
                        end = min(duration, (stamp(run['finished_utc']) - stamp(anchor)).total_seconds())
                        if end > begin:
                            clip = [round(begin, 3), round(end, 3)]
                    if hotel_rule and mapname==hotel_rule['map'] and media['file']==hotel_rule['source']:
                        if clip is None or clip[1]<=hotel_rule['minimum_candidate_source_seconds']:
                            continue
                        clip[0]=max(clip[0],hotel_rule['minimum_candidate_source_seconds'])
                    candidates.append({
                        'file': media['file'], 'candidate_source_seconds': clip,
                        'round_id': run.get('round_id'),
                        'round_match_verified': bool(winning_round and run.get('round_id') == winning_round),
                        'exact_controller_finalization_path': run.get('exact_finalization_path', False),
                        'game_t_range': [run.get('start_game_t'), run.get('end_game_t')],
                        'pause_and_boundary_review': 'pending', 'privacy_review': 'pending',
                        'audio_review': 'pending', 'edit_selection': 'pending',
                    })
            chapters.append({
                'campaign': campaign, 'map': mapname, 'verified_outcome': chapter['outcome'],
                'winning_round': winning_round, 'completion_evidence': evidence.name,
                'completed_on_chapter_run': chapter.get('completed_on_chapter_run'),
                'candidates': candidates,
                'manual_association_needed': not winning_round or not candidates,
                'transition_and_outro_helper_coverage': 'pending',
                'timeline_selection_evidence':hotel_rule['evidence'] if hotel_rule and mapname==hotel_rule['map'] else None,
            })
    report = {
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'inventory_created_utc': inventory['created_utc'], 'draft_only': True,
        'ready_to_render': False, 'ready_to_publish': False,
        'chapters': chapters,
        'requirements': [
            'Preserve every original recording.',
            'Use successful finale attempts and disclose independently verified attempt counts.',
            'Retain the story chapters; remove pauses, setup and unnecessary waiting.',
            'Do not restore the blanket privacy box; old baked pixels cannot be reconstructed.',
            'Check exact footage boundaries and account aliases, including credits and notifications.',
            'Use authentic public commentary or clearly labeled retrospective narration.',
            'Review the entire actual final export, audio and publication metadata.',
        ],
    }
    out = ROOT / 'private/postproduction/successful-attempt-edit-candidates.json'
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'verified_chapters': len(chapters),
                      'candidate_segments': sum(len(x['candidates']) for x in chapters),
                      'manual_association_chapters': sum(x['manual_association_needed'] for x in chapters),
                      'ready_to_publish': False}))


if __name__ == '__main__':
    build()
