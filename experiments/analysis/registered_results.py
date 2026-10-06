"""Audit registration coverage and produce descriptive paired pilot results.

This is analysis-only: it never grades task artifacts, changes a candidate, or
feeds held-out results to the executor. A partial study cannot be labeled final.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT/'experiments/results'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def measured_tokens(rows):
    totals = Counter()
    for row in rows:
        for request in row['requests']:
            usage = request.get('usage')
            if not usage:
                totals['usage_missing'] += 1
                continue
            totals['usage_present'] += 1
            for key in ('input_tokens', 'output_tokens', 'total_tokens'):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    totals[key] += value
                else:
                    totals[key+'_missing'] += 1
            cached = (usage.get('input_tokens_details') or {}).get('cached_tokens')
            if type(cached) is int and cached >= 0:
                totals['cached_input_tokens'] += cached
            else:
                totals['cached_input_tokens_missing'] += 1
    return dict(totals)


def aggregate(rows):
    return {'n': len(rows), 'artifact_pass': sum(r['passed'] for r in rows),
            'terminal_stops': sum(r['declared_complete'] for r in rows),
            'unmet_terminal_stop': sum(r['false_completion'] for r in rows),
            'underclaim': sum(r['underclaim'] for r in rows),
            'statuses': dict(Counter(r['status'] for r in rows)),
            'multi_episode_runs': sum(len(r['episodes']) > 1 for r in rows),
            'episodes': sum(len(r['episodes']) for r in rows),
            'extra_episodes_after_success': sum(r.get('extra_episodes_after_success') or 0 for r in rows),
            'executor_logical_calls': sum(r['executor_calls'] for r in rows),
            'physical_requests': sum(len(r['requests']) for r in rows),
            'transport_failures': sum(r['transport_failures'] for r in rows),
            'rejected_requests': sum(len(r['rejected_requests']) for r in rows),
            'rejected_request_types': dict(Counter(
                f'{x.get("path", "budget")}: {x.get("reason", "not-allowlisted")}'
                for r in rows for x in r['rejected_requests'])),
            'runs_with_recorded_errors': sum(
                r['status'] in {'infrastructure_error', 'supervisor_error', 'wall_timeout'} or
                any(e.get('error') for e in r['episodes']) for r in rows),
            'seconds_sum': sum(r['seconds'] for r in rows),
            'seconds_median': statistics.median(r['seconds'] for r in rows) if rows else None,
            'queue_seconds_sum': sum(r['queue_seconds'] for r in rows),
            'episode_seconds_sum': sum(e['seconds'] for r in rows for e in r['episodes']),
            'tokens': measured_tokens(rows)}


def audit(registration_path, batch_paths):
    registration = read(registration_path)
    manifest_path = Path(registration_path).parent/registration['manifest_file']
    manifest = read(manifest_path)
    errors, rows, seen = [], [], set()
    if sha(manifest_path) != registration['manifest_file_sha256']:
        errors.append('Frozen manifest does not match registration')
    registered_at = datetime.fromisoformat(registration['registered_utc']).timestamp()
    expected = {}
    for batch in registration['batches']:
        for task_id, task_sha in manifest['reserved_tasks'].items():
            if int(task_id.rsplit('-', 1)[1]) not in batch['seeds']:
                continue
            for arm in registration['arms']:
                expected[f'{batch["tag"]}-{task_id}-{arm}'] = (batch['tag'], task_id, arm, task_sha)
    for path in batch_paths:
        for r in read(path):
            run_id = r['run_id']
            if run_id in seen:
                errors.append('Duplicate run: '+run_id)
            seen.add(run_id)
            if run_id not in expected:
                errors.append('Unregistered run: '+run_id)
                continue
            tag, task_id, arm, task_sha = expected[run_id]
            if (r['task_id'], r['arm'], r['task_sha256']) != (task_id, arm, task_sha):
                errors.append('Task or arm identity mismatch: '+run_id)
            if r['started'] < registered_at:
                errors.append('Run predates registration: '+run_id)
            if len(r['episodes']) > manifest['limits']['outer_turns']:
                errors.append('Outer turn budget exceeded: '+run_id)
            if r['executor_calls'] > manifest['limits']['executor_calls']:
                errors.append('Logical call budget exceeded: '+run_id)
            if len(r['requests']) > manifest['limits']['all_physical_calls']:
                errors.append('Physical call budget exceeded: '+run_id)
            expected_pass = bool(r['episodes']) and r['episodes'][-1]['score']['passed']
            if r['passed'] != expected_pass or r['false_completion'] != (r['declared_complete'] and not r['passed']):
                errors.append('Outcome fields disagree: '+run_id)
            if [x['index'] for x in r['requests']] != list(range(1, len(r['requests'])+1)):
                errors.append('Non-contiguous physical request records: '+run_id)
            if any(x['request_model'] != manifest['model'] for x in r['requests']):
                errors.append('Different requested model: '+run_id)
            unexpected = [x.get('response_model') for x in r['requests']
                          if x.get('response_model') not in (None, manifest['model'])]
            if unexpected:
                errors.append('Different reported model: '+run_id)
            r = dict(r, batch=tag)
            rows.append(r)
    plan_paths = []
    if 'within_batch_order_seed' in registration:
        families = list(dict.fromkeys(k.rsplit('-', 1)[0] for k in manifest['reserved_tasks']))
        for batch in registration['batches']:
            path = Path(registration_path).parent/(batch['tag']+'-plan.json')
            if not path.exists():
                errors.append('Missing dispatch plan: '+batch['tag'])
                continue
            plan_paths.append(path)
            plan = read(path)
            intended = []
            rng = random.Random(registration['within_batch_order_seed'])
            for seed in batch['seeds']:
                for family in families:
                    block = [[family, seed, arm] for arm in registration['arms']]
                    rng.shuffle(block)
                    intended.extend(block)
            if plan != intended:
                errors.append('Dispatch plan differs from registered randomization: '+batch['tag'])
            observed = [[r['family'], r['seed'], r['arm']] for r in rows if r['batch'] == batch['tag']]
            if observed != plan[:len(observed)]:
                errors.append('Observed run order differs from dispatch plan: '+batch['tag'])
    missing = sorted(set(expected)-seen)
    arms = {arm: aggregate([r for r in rows if r['arm'] == arm]) for arm in registration['arms']}
    pairs = []
    indexed = {(r['batch'], r['task_id'], r['arm']): r for r in rows}
    for other in registration['arms']:
        if other == 'sg_v2':
            continue
        for batch in registration['batches']:
            for task_id in manifest['reserved_tasks']:
                left = indexed.get((batch['tag'], task_id, 'sg_v2'))
                right = indexed.get((batch['tag'], task_id, other))
                if left and right:
                    pairs.append({'batch': batch['tag'], 'task_id': task_id, 'comparison': other,
                                  'v2_pass': left['passed'], 'other_pass': right['passed'],
                                  'physical_request_difference': len(left['requests'])-len(right['requests']),
                                  'wall_seconds_difference': left['seconds']-right['seconds']})
    comparisons = {}
    for other in registration['arms']:
        group = [p for p in pairs if p['comparison'] == other]
        if group:
            comparisons[other] = {
                'paired_samples': len(group),
                'v2_only_pass': sum(p['v2_pass'] and not p['other_pass'] for p in group),
                'other_only_pass': sum(p['other_pass'] and not p['v2_pass'] for p in group),
                'both_pass': sum(p['v2_pass'] and p['other_pass'] for p in group),
                'neither_pass': sum(not p['v2_pass'] and not p['other_pass'] for p in group),
                'mean_physical_request_difference': statistics.mean(p['physical_request_difference'] for p in group)}
    return {'kind': 'descriptive_registered_pilot_analysis',
            'generated_utc': datetime.now(UTC).isoformat(),
            'complete': not errors and not missing and len(rows) == registration['runs'],
            'expected_runs': registration['runs'], 'observed_runs': len(rows),
            'missing_runs': missing, 'audit_errors': errors,
            'distinct_procedural_fixtures': len(manifest['reserved_tasks']),
            'families': {f: aggregate([r for r in rows if r['family'] == f])
                         for f in sorted({r['family'] for r in rows})},
            'arms': arms, 'total': aggregate(rows), 'comparisons': comparisons, 'paired_outcomes': pairs,
            'input_sha256': {str(Path(p).resolve().relative_to(ROOT)): sha(p)
                             for p in [registration_path, manifest_path, *batch_paths, *plan_paths]},
            'analysis_sha256': sha(__file__),
            'limits': ['Descriptive pilot; no confirmatory significance test or generality claim.',
                       'Nine related procedural fixtures, repeated twice; not 108 independent tasks.',
                       'Synthesis prose quality and unseen data generalization are not fully graded.',
                       'A missing usage record contributes no known tokens and must be reported separately.',
                       'Model alias is not an immutable checkpoint; billing is not verified.',
                       'One-episode runs do not exercise task-state context reinforcement.']}


def markdown(result):
    lines = ['# Registered pilot results', '',
             (f'Status: {"complete" if result["complete"] else "INCOMPLETE OR AUDIT FAILED"}; '
              f'{result["observed_runs"]}/{result["expected_runs"]} runs.'), '',
             '| Arm | Outcome pass | Unmet terminal stop | Multi-episode | Physical requests | Known tokens | Missing usage | Median wall s |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for arm, a in result['arms'].items():
        median = f'{a["seconds_median"]:.1f}' if a['seconds_median'] is not None else '—'
        lines.append(f'| {arm} | {a["artifact_pass"]}/{a["n"]} | {a["unmet_terminal_stop"]} | '
                     f'{a["multi_episode_runs"]} | {a["physical_requests"]} | '
                     f'{a["tokens"].get("total_tokens", 0):,} | {a["tokens"].get("usage_missing", 0)} | {median} |')
    lines += ['', 'The table counts requests forwarded to the model provider, including judges and retries.',
              f'Local rejected HTTP attempts are reported separately: {result["total"]["rejected_requests"]}.',
              'These local rejections were not forwarded and are not assigned model token usage.',
              'Token totals are provider-reported',
              'input plus output usage, including cached input; they are not an invoice or unique-text count.',
              'Wall time includes queueing, environment startup, checks and SDK work.', '',
              '## Paired outcomes', '',
              '| V2 compared with | Pairs | V2 only passes | Other only passes | Both pass | Neither passes |',
              '|---|---:|---:|---:|---:|---:|']
    for arm, a in result['comparisons'].items():
        lines.append(f'| {arm} | {a["paired_samples"]} | {a["v2_only_pass"]} | '
                     f'{a["other_only_pass"]} | {a["both_pass"]} | {a["neither_pass"]} |')
    lines += ['', *('- '+s for s in result['limits'])]
    if result['audit_errors']:
        lines += ['', 'Audit errors:', *('- '+s for s in result['audit_errors'])]
    return '\n'.join(lines)+'\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, default=RESULTS/'registration-holdout01.json')
    parser.add_argument('--batches', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, default=RESULTS/'holdout01-analysis.json')
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    result = audit(args.registration, args.batches)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(markdown(result), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('complete', 'expected_runs', 'observed_runs', 'audit_errors')}))
    if result['audit_errors'] or (not result['complete'] and not args.allow_partial):
        raise SystemExit(1)
