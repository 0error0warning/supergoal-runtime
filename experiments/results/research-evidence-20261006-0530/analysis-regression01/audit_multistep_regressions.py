"""Audit same-test-ID transitions from saved official logs, without model calls."""
import argparse
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_outcomes(text):
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    collected = re.findall(r'^collected (\d+) items?$', text, re.MULTILINE)
    outcomes, duplicates = {}, []
    for line in text.splitlines():
        match = re.match(r'^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (.+)$', line)
        if not match:
            continue
        status, name = match.groups()
        name = name.split(' - ', 1)[0]
        if '::' not in name:
            continue
        if name in outcomes:
            duplicates.append(name)
        outcomes[name] = status
    complete = len(collected) == 1 and len(outcomes) == int(collected[0]) and not duplicates
    return {'complete_test_id_inventory': complete,
            'collected': int(collected[0]) if len(collected) == 1 else None,
            'parsed': len(outcomes), 'duplicate_ids': duplicates, 'outcomes': outcomes,
            'outcome_counts': dict(Counter(outcomes.values()))}


def transitions(before, after):
    if not before['complete_test_id_inventory'] or not after['complete_test_id_inventory']:
        return {'status': 'unresolved_incomplete_log_inventory'}
    old, new = before['outcomes'], after['outcomes']
    common = old.keys() & new.keys()
    return {'status': 'measured', 'common_test_ids': len(common),
            'old_ids_not_repeated': sorted(old.keys() - new.keys()),
            'pass_to_fail': sorted(k for k in common if old[k] == 'PASSED' and new[k] == 'FAILED'),
            'pass_to_error': sorted(k for k in common if old[k] == 'PASSED' and new[k] == 'ERROR'),
            'fail_to_pass': sorted(k for k in common if old[k] == 'FAILED' and new[k] == 'PASSED')}


def audit(root, registration):
    label = registration['experiment']
    tasks = {task['task_id']: task for task in registration['tasks']}
    verified = {}
    for task in tasks.values():
        source = Path(task['path'])
        current = {p.relative_to(source).as_posix(): sha(p) for p in sorted(source.rglob('*')) if p.is_file()}
        if current != task['files_sha256']:
            raise ValueError('Registered task source changed: ' + task['task_id'])
        verified[task['task_id']] = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
    rows = []
    for index, cell in enumerate(registration['planned_order']):
        job = root / 'jobs' / f"{label}-{index+1:02d}-{cell['task_id']}-{cell['arm']}"
        results = list(job.glob('*/result.json'))
        if len(results) > 1:
            raise ValueError('Multiple outcomes for one registered trajectory')
        row = {**cell, 'status': 'not_returned', 'checkpoints': [], 'transitions': []}
        rows.append(row)
        if not results:
            continue
        path = results[0]
        raw = json.loads(path.read_text())
        row.update(status='returned', result_path=str(path), result_sha256=sha(path))
        previous = None
        for step in raw.get('step_results', []):
            name = step['step_name']
            if not re.fullmatch(r'checkpoint_\d+', name):
                raise ValueError('Invalid checkpoint name')
            log = path.parent / 'steps' / name / 'verifier/test-stdout.txt'
            if not log.exists():
                row['checkpoints'].append({'checkpoint': name, 'status': 'missing_log'})
                previous = None
                continue
            measured = parse_outcomes(log.read_text(errors='replace'))
            measured.update(checkpoint=name, stdout_sha256=sha(log), stdout_path=str(log))
            row['checkpoints'].append(measured)
            if previous:
                row['transitions'].append({'from_checkpoint': previous['checkpoint'], 'to_checkpoint': name,
                                           **transitions(previous, measured)})
            previous = measured
    return {'experiment': label, 'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source_trees_verified_sha256': verified, 'rows': rows, 'model_calls': 0,
            'raw_scores_unchanged': True,
            'scope': 'Same-ID transitions in fixed upstream tests. Runtime checkpoint context changes; this is a regression signal, '
                     'not an artifact-only causal experiment or an independent task count. Missing inventories stay unresolved.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/var/lib/supergoal-lab'))
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root, json.loads(args.registration.read_text()))
    report['operator_sha256'] = sha(Path(__file__))
    report['registration_sha256'] = sha(args.registration)
    with args.output.open('x') as file:
        json.dump(report, file, indent=2)
    print(json.dumps({'output': str(args.output), 'returned': sum(r['status'] == 'returned' for r in report['rows']),
                      'unresolved_inventories': sum(not c.get('complete_test_id_inventory')
                                                   for r in report['rows'] for c in r['checkpoints'])}))


if __name__ == '__main__':
    main()
