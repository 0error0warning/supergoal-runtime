"""Describe task-paired outcomes without equating research delivery with quality."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def summarize_row(row, report, journal):
    report = report or {}
    records = (journal or {}).get('records', report.get('requests', []))
    charged = len(records) if journal is not None else report.get('used_requests')
    measured = [r['usage'] for r in records if isinstance(r.get('usage'), dict)
                and all(type(r['usage'].get(k)) is int for k in ['input_tokens', 'output_tokens'])]
    usage_complete = charged is not None and charged == len(records) == len(measured)
    research = row['task_id'].startswith('dr3-')
    reward = (row.get('raw_rewards') or {}).get('delivery_present' if research else 'reward')
    returned_grade = type(reward) in {int, float} and not row.get('exception')
    started = bool(row.get('started_at')) or row['status'] == 'operator_exception'
    online = (report.get('last_acceptance') or {}).get('verdict')
    rounds = report.get('rounds', [])
    calls = Counter()
    for episode in rounds:
        calls[episode['role']] += episode['model_calls']
    seconds = None
    if row.get('started_at') and row.get('finished_at'):
        seconds = (datetime.datetime.fromisoformat(row['finished_at'])
                   - datetime.datetime.fromisoformat(row['started_at'])).total_seconds()
    return {'index': row['index'], 'task_id': row['task_id'], 'arm': row['arm'],
        'kind': 'research' if research else 'terminal', 'row_status': row['status'],
        'exception': row.get('exception'), 'controller_status': report.get('status'),
        'raw_terminal_reward': None if research else reward,
        'research_delivery_present': reward if research else None,
        'official_terminal_grade_returned': returned_grade and not research,
        'terminal_end_to_end_success': (int(returned_grade and reward == 1) if started and not research else None),
        'requests': charged,
        'observed_input_tokens': sum(r['usage']['input_tokens'] for r in records
            if type((r.get('usage') or {}).get('input_tokens')) is int),
        'observed_output_tokens': sum(r['usage']['output_tokens'] for r in records
            if type((r.get('usage') or {}).get('output_tokens')) is int),
        'input_tokens': sum(u['input_tokens'] for u in measured) if usage_complete else None,
        'output_tokens': sum(u['output_tokens'] for u in measured) if usage_complete else None,
        'usage_complete': usage_complete, 'requests_missing_usage': charged - len(measured) if charged is not None else None,
        'calls_by_role': dict(calls), 'role_counts': dict(Counter(r['role'] for r in rounds)),
        'executor_episodes': sum(r['role'] == 'executor' for r in rounds),
        'episode_seconds': sum(r['seconds'] for r in rounds), 'trial_wall_seconds': seconds,
        'transport_error_calls': sum(bool(r.get('error')) for r in records),
        'online_verdict': online,
        'false_online_acceptance': bool(not research and returned_grade and reward == 0 and online == 'pass'),
        'false_online_rejection': bool(not research and returned_grade and reward == 1 and online == 'fail'),
        'unknown_with_passing_artifact': bool(not research and returned_grade and reward == 1 and online == 'unknown'),
        'brief_route': (report.get('goal_brief') or {}).get('route'),
        'brief_authority': (report.get('goal_brief') or {}).get('authority'),
        'brief_requirement_count': len((report.get('goal_brief') or {}).get('requirements', [])),
        'interpretation_attempts': report.get('interpretation_attempts', []),
        'audit_format_errors': sum(bool(a.get('coverage_error')) for a in report.get('coverage_audits', [])),
        'audit_unknown_causes': dict(Counter(r.get('unknown_kind') for a in report.get('coverage_audits', [])
            for r in a.get('requirements', []) if r['verdict'] == 'unknown'))}


def analyze(capsule):
    manifests = read(capsule / 'manifest.json')
    label = manifests['experiment']
    reg = read(capsule / 'setup' / ('registration-' + label + '.json'))
    receipt = read(capsule / 'setup' / (label + '-receipt.json'))
    rows = []
    for row in receipt['rows']:
        uid = ((row.get('agent_result') or {}).get('metadata') or {}).get('control_id')
        control = capsule / 'control' / (uid or 'unavailable')
        rows.append(summarize_row(row, read(control / 'report.json'), read(control / 'request-journal.json')))
    arms = reg.get('arms') or list(dict.fromkeys(r['arm'] for r in rows))
    aggregates = []
    for kind in ['terminal', 'research']:
        for arm in arms:
            selected = [r for r in rows if r['kind'] == kind and r['arm'] == arm]
            aggregates.append({'kind': kind, 'arm': arm, 'observed_rows': len(selected),
                'terminal_successes': sum(r['terminal_end_to_end_success'] == 1 for r in selected) if kind == 'terminal' else None,
                'terminal_started': sum(r['terminal_end_to_end_success'] is not None for r in selected) if kind == 'terminal' else None,
                'research_files_delivered': sum(r['research_delivery_present'] == 1 for r in selected) if kind == 'research' else None,
                'observed_requests': sum(r['requests'] for r in selected if r['requests'] is not None),
                'requests_complete': all(r['requests'] is not None for r in selected),
                'input_tokens': sum(r['input_tokens'] for r in selected) if all(r['usage_complete'] for r in selected) else None,
                'output_tokens': sum(r['output_tokens'] for r in selected) if all(r['usage_complete'] for r in selected) else None,
                'false_online_acceptances': sum(r['false_online_acceptance'] for r in selected),
                'false_online_rejections': sum(r['false_online_rejection'] for r in selected),
                'unknown_with_passing_artifact': sum(r['unknown_with_passing_artifact'] for r in selected),
                'extra_executor_episodes': sum(max(0, r['executor_episodes'] - 1) for r in selected)})
    lookup = {(r['task_id'], r['arm']): r for r in rows}
    pairs = []
    for other in [a for a in arms if a != 'evidence']:
        compared = []
        for task in [t['task_id'] for t in reg['tasks'] if not t['task_id'].startswith('dr3-')]:
            a, b = lookup.get((task, other)), lookup.get((task, 'evidence'))
            if a and b and all(r['terminal_end_to_end_success'] is not None for r in [a, b]):
                compared.append({'task_id': task, 'other_success': a['terminal_end_to_end_success'],
                    'evidence_success': b['terminal_end_to_end_success'], 'other_requests': a['requests'],
                    'evidence_requests': b['requests']})
        pairs.append({'other_arm': other, 'paired_tasks': len(compared),
            'evidence_only_success': sum(p['evidence_success'] > p['other_success'] for p in compared),
            'other_only_success': sum(p['other_success'] > p['evidence_success'] for p in compared),
            'rows': compared})
    input_integrity = []
    for row in rows:
        if row['kind'] != 'research':
            continue
        integrity = read(capsule / 'research-grading' / label / ('submission-' + str(row['index'])) / 'input-integrity.json')
        if integrity:
            expected, observed = integrity['expected'], integrity['observed']
            changed = [name for name in expected.keys() & observed.keys() if expected[name] != observed[name]]
            removed = sorted(expected.keys() - observed.keys())
            input_integrity.append({'index': row['index'], 'task_id': row['task_id'], 'arm': row['arm'],
                'original_files_unchanged': not changed and not removed, 'changed': sorted(changed), 'removed': removed,
                'added': sorted(observed.keys() - expected.keys()), 'whole_tree_identical': expected == observed})
    return {'experiment': label, 'cohort_status': receipt['status'], 'planned_trials': len(reg['planned_order']),
        'observed_trials': len(rows), 'aggregates': aggregates, 'paired_terminal': pairs, 'rows': rows,
        'research_diagnostics': read(capsule / 'research-grading' / label / 'diagnostics.json'),
        'research_input_integrity': input_integrity,
        'limits': ['One run per task/arm; small task sample, no statistical superiority claim',
                   'Research file delivery is not quality or a terminal reward',
                   'Research diagnostics use the same base model, a request-only rubric, and a non-exhaustive claim sample',
                   'Common ceilings do not imply equal actual compute; missing usage remains unknown']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('capsule', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(analyze(args.capsule), stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
