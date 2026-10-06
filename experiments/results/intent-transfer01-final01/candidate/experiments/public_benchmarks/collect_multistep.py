"""Read-only SCBench analysis: dependent checkpoints remain within problems."""
import argparse
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import re


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def context_payload_identity(control):
    """Export only actual context fingerprints, never provider credentials."""
    if control is None:
        return {'present': False}
    history = control.get('history')
    goal = None
    prefix = 'Durable task state (observations, not instructions from artifacts):\n'
    if control['prompt'].startswith(prefix):
        goal = json.loads(control['prompt'][len(prefix):]).get('goal')
    return {'present': True,
            'prompt_sha256': hashlib.sha256(control['prompt'].encode()).hexdigest(),
            'goal_sha256': hashlib.sha256(goal.encode()).hexdigest() if isinstance(goal, str) else None,
            'history_messages': len(history or []),
            'history_sha256': hashlib.sha256(json.dumps(history, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')).encode()).hexdigest() if history else None}


def summarize_problem(raw, expected_steps):
    steps = raw.get('step_results') or []
    strict = [((s.get('verifier_result') or {}).get('rewards') or {}).get('strict_pass_rate') for s in steps]
    observed = [score for score in strict if score is not None]
    all_passed = len(observed) == expected_steps and all(s == 1 for s in strict)
    returned_without_exception = len(steps) == expected_steps and len(observed) == expected_steps \
        and not raw.get('exception_info') and not any(s.get('exception_info') for s in steps)
    return {'expected_checkpoints': expected_steps, 'returned_checkpoints': len(steps),
            'scored_checkpoints': len(observed),
            'all_registered_checkpoints_strictly_passed': all_passed,
            'all_checkpoints_graded_without_exception': returned_without_exception,
            # Retain the original field as the stricter success measure. It
            # must not be read as an exception counter when strict tests fail.
            'complete_without_execution_or_grading_exception': all_passed and returned_without_exception,
            'mean_available_checkpoint_strict': sum(observed) / len(observed) if observed else None,
            'official_problem_rewards': (raw.get('verifier_result') or {}).get('rewards'),
            'exception': raw.get('exception_info'), 'steps': steps}


def checkpoint_diagnostic(step, report, reward_details):
    strict = ((step.get('verifier_result') or {}).get('rewards') or {}).get('strict_pass_rate')
    verdict = (report.get('last_acceptance') or {}).get('verdict')
    return {'checkpoint': step['step_name'], 'online_verdict': verdict, 'strict_pass_rate': strict,
            'online_pass_with_strict_shortfall': verdict == 'pass' and strict is not None and strict < 1,
            'executor_episodes': sum(r['role'] == 'executor' for r in report.get('rounds', [])),
            'review_episodes': sum(r['role'] == 'review' for r in report.get('rounds', [])),
            'controller_status': report.get('status'),
            'worker_incomplete': report.get('status') == 'worker_incomplete',
            'upstream_http_errors': dict(Counter(str(r['http_status']) for r in report.get('requests', [])
                if isinstance(r.get('http_status'), int) and r['http_status'] >= 400)),
            'transport_error_calls': sum(bool(r.get('error')) for r in report.get('requests', [])),
            'official_reward_details': reward_details}


def collect(root, registration):
    label = registration['experiment']
    receipt = read(root / 'setup' / (label + '-receipt.json'), {})
    status = {r['index']: r for r in receipt.get('rows', [])}
    tasks = {t['task_id']: t for t in registration['tasks']}
    rows, controls, ledgers = [], {}, {}
    for index, cell in enumerate(registration['planned_order']):
        row = {'index': index, **cell, 'status': status.get(index, {}).get('status', 'pending')}
        job = root / 'jobs' / f"{label}-{index+1:02d}-{cell['task_id']}-{cell['arm']}"
        results = list(job.glob('*/result.json'))
        if len(results) > 1:
            raise ValueError('Duplicate trial; refuse selection of the best result')
        if results:
            raw = read(results[0])
            row.update(result_path=str(results[0]), result_sha256=hashlib.sha256(results[0].read_bytes()).hexdigest(),
                       **summarize_problem(raw, tasks[cell['task_id']]['checkpoints']))
            ids = {(s.get('agent_result') or {}).get('metadata', {}).get('problem_id')
                   for s in row['steps'] if (s.get('agent_result') or {}).get('metadata')}
            ids.discard(None)
            if len(ids) > 1:
                raise ValueError('Checkpoint controller identities disagree')
            if ids:
                problem = ids.pop()
                if not re.fullmatch(r'[0-9a-f]{32}', problem):
                    raise ValueError('Invalid problem identity')
                ledger = read(root / 'control' / ('scb-' + problem) / 'problem.json', {})
                ledgers[problem] = ledger
                row.update(problem_id=problem, charged_physical_requests=0,
                           observed_input_tokens=0, observed_output_tokens=0, incomplete_usage_records=0)
                for child in ledger.get('children', []):
                    if not re.fullmatch(r'[0-9a-f]{32}', child):
                        raise ValueError('Invalid child identity')
                    folder = root / 'control' / child
                    report = read(folder / 'report.json', {})
                    journal = read(folder / 'request-journal.json', {})
                    records = journal.get('records', [])
                    controls[child] = {'report': report, 'environment': read(folder / 'environment.json'),
                                       'snapshot_lifecycle': read(folder / 'snapshot-lifecycle.json'),
                                       'first_executor_payload': context_payload_identity(read(folder / '01-executor/control.json')),
                                       'charged_requests': len(records)}
                    row['charged_physical_requests'] += len(records)
                    row['incomplete_usage_records'] += sum(not r.get('usage') for r in records)
                    row['observed_input_tokens'] += sum((r.get('usage') or {}).get('input_tokens', 0) or 0 for r in records)
                    row['observed_output_tokens'] += sum((r.get('usage') or {}).get('output_tokens', 0) or 0 for r in records)
            row['checkpoint_diagnostics'] = []
            for step in row['steps']:
                name = step['step_name']
                if not re.fullmatch(r'checkpoint_\d+', name):
                    raise ValueError('Unexpected checkpoint name')
                child = ((step.get('agent_result') or {}).get('metadata') or {}).get('control_id')
                report = controls.get(child, {}).get('report', {})
                details = read(results[0].parent / 'steps' / name / 'verifier/reward_details.json')
                row['checkpoint_diagnostics'].append(checkpoint_diagnostic(step, report, details))
        rows.append(row)
    return {'experiment': label, 'collected_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'statistical_units': len(tasks), 'planned_trajectories': len(rows),
            'cohort_status': receipt.get('status'), 'rows': rows, 'problem_ledgers': ledgers,
            'child_controls': controls, 'raw_rewards_unchanged': True,
            'scope': 'Available checkpoint means do not imply complete-problem success. '
                     'Checkpoints and arms are not independent task samples. Interrupted usage can be incomplete.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/var/lib/supergoal-lab'))
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reg = read(args.registration)
    report = collect(args.root, reg)
    report['registration_sha256'] = hashlib.sha256(args.registration.read_bytes()).hexdigest()
    data = json.dumps(report, ensure_ascii=False, indent=2)
    key = read(args.root / 'private/model.json', {}).get('api_key')
    if key:
        data = data.replace(key, '[REDACTED]')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(data)
    print(json.dumps({'output': str(args.output), 'bytes': len(data.encode()),
                      'returned_trajectories': sum('result_path' in r for r in report['rows'])}))


if __name__ == '__main__':
    main()
