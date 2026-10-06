"""Read-only identity and cumulative-budget audit for the finished context study."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit_problem(root, row, task, reg, known):
    result_path = Path(row['result_path']).resolve()
    job = Path(row['job']).resolve()
    require(job.parent == root / 'jobs' and result_path.is_relative_to(job), 'Job path escaped')
    require(list(job.glob('*/result.json')) == [result_path], 'Missing or duplicate result')
    result = read(result_path)
    steps = result.get('step_results') or []
    require(len(steps) == task['checkpoints'], 'Incomplete checkpoint inventory')
    metadata = [(s.get('agent_result') or {}).get('metadata') or {} for s in steps]
    ids = {m.get('problem_id') for m in metadata}
    require(len(ids) == 1, 'Problem identity differs between checkpoints')
    uid = ids.pop()
    require(isinstance(uid, str) and re.fullmatch(r'[a-f0-9]{32}', uid) and uid not in known, 'Invalid or reused problem')
    known.add(uid)
    directory = root / 'control' / ('scb-' + uid)
    ledger_path = directory / 'problem.json'
    ledger = read(ledger_path)
    require(ledger['problem_id'] == uid and ledger['arm'] == row['arm'], 'Problem ledger identity changed')
    require(ledger['max_problem_requests'] == reg['max_problem_requests'], 'Problem ceiling changed')
    require(len(ledger['steps']) == task['checkpoints'], 'Ledger checkpoint inventory differs')
    require(len(ledger['grader_history_boundaries']) == task['checkpoints'], 'History boundary inventory missing')
    children, total, boundaries = [], 0, []
    for index, (step, meta, state, boundary) in enumerate(zip(steps, metadata, ledger['steps'], ledger['grader_history_boundaries']), 1):
        require(step['step_name'] == f'checkpoint_{index}' and state['checkpoint'] == index, 'Checkpoint order changed')
        require(meta['study_arm'] == row['arm'], 'Checkpoint arm changed')
        require(state['requests_before'] == total, 'Cumulative budget before checkpoint disagrees')
        backup = directory / f'grader-history-{index}.json'
        require(boundary['checkpoint'] == index and sha(backup) == boundary['backup_sha256'], 'History backup changed')
        original = read(backup)
        restored = boundary.get('restoration') or {}
        require(boundary['status'] == 'concealed' and restored.get('status') == 'restored'
                and original.get('sha256') == boundary.get('original_sha256') == restored.get('sha256')
                and original['exists'] == restored['original_exists'], 'History boundary failed')
        boundaries.append({'checkpoint': index, 'backup_sha256': sha(backup),
                           'original_sha256': original.get('sha256'), 'restoration': restored})
        child = state.get('control_id')
        if not child:
            require(total == reg['max_problem_requests'] and state['status'] == 'problem_budget_exhausted'
                    and meta['physical_requests'] == 0, 'Unexplained missing child controller')
            continue
        require(re.fullmatch(r'[a-f0-9]{32}', child) and child not in known, 'Invalid or reused child controller')
        known.add(child)
        child_root = root / 'control' / child
        report_path, journal_path = child_root / 'report.json', child_root / 'request-journal.json'
        report, journal = read(report_path), read(journal_path)
        require(report['control_id'] == child == meta['control_id'] and report['arm'] == row['arm'], 'Child identity differs')
        ceiling = min(reg['max_step_requests'], reg['max_problem_requests'] - total)
        require(report['max_requests'] == journal['limit'] == ceiling, 'Child ceiling differs')
        used = len(journal['records'])
        require(used == report['used_requests'] == state['physical_requests'] == meta['physical_requests']
                and 0 <= used <= ceiling, 'Child request accounting differs')
        require([r['index'] for r in journal['records']] == list(range(1, used + 1)), 'Journal sequence differs')
        models = Counter(r.get('request_model') for r in journal['records'])
        require(set(models) <= {reg['model']}, 'Unexpected model requested')
        require(len(report.get('requests', [])) <= used, 'Report contains uncharged requests')
        total += used
        require(total == state['requests_after'] == meta['problem_requests_total'], 'Cumulative budget after checkpoint differs')
        children.append({'checkpoint': index, 'control_id': child, 'requests': used, 'ceiling': ceiling,
                         'request_models': dict(models), 'report_sha256': sha(report_path),
                         'journal_sha256': sha(journal_path), 'status': report['status'],
                         'raw_rewards': (step.get('verifier_result') or {}).get('rewards'),
                         'exception_type': (step.get('exception_info') or {}).get('exception_type')})
    require(ledger['children'] == [r['control_id'] for r in children], 'Child inventory differs')
    require(ledger['reserved_requests'] == total <= reg['max_problem_requests'], 'Problem charge differs')
    return {'task_id': row['task_id'], 'arm': row['arm'], 'problem_id': uid,
            'requests': total, 'result_sha256': sha(result_path), 'ledger_sha256': sha(ledger_path),
            'children': children, 'grader_history_boundaries': boundaries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/var/lib/supergoal-lab'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Preserve prior audits')
    root = args.root.resolve()
    label = 'context-handoff01'
    registration = root / 'setup' / f'registration-{label}.json'
    receipt_path = root / 'setup' / f'{label}-receipt.json'
    reg, receipt = read(registration), read(receipt_path)
    require(receipt['status'] == 'complete_with_itemized_outcomes', 'Cohort unfinished')
    require(receipt['registration_sha256'] == sha(registration), 'Registration changed')
    require(receipt['automatic_retries'] == reg['automatic_retries'] == 0, 'Retry policy changed')
    candidate = Path(reg['candidate'])
    source_hashes = {p.relative_to(candidate).as_posix(): sha(p) for p in candidate.rglob('*.py')}
    require(source_hashes == reg['source_sha256'], 'Frozen Python source inventory differs')
    pinned = {'integration_gate_sha256': root / 'setup/context-smoke01.json',
              'reference_receipt_sha256': root / 'setup/scb-oracle01.json',
              'reference_task_registration_sha256': root / 'setup/registration-scb-transfer01.json',
              'grader_history_test_junit_sha256': root / 'grader-history-checks01/junit.xml',
              'protocol_sha256': candidate / 'docs/context-handoff-study-2026-10-06.md',
              'candidate_archive_sha256': root / 'setup/context-handoff01-source.tar.gz'}
    for key, path in pinned.items():
        require(sha(path) == reg[key], 'Pinned source changed: ' + key)
    for task in reg['tasks']:
        source = Path(task['path'])
        files = {p.relative_to(source).as_posix(): sha(p) for p in source.rglob('*') if p.is_file()}
        require(files == task['files_sha256'], 'Task source changed: ' + task['task_id'])
        image = subprocess.check_output(['docker', '-H', 'unix://' + str(root / 'run/docker.sock'),
                                         'image', 'inspect', task['initial_image_id'], '--format', '{{.Id}}'], text=True).strip()
        require(image == task['initial_image_id'], 'Initial image differs')
    rows = receipt['rows']
    expected = [(r['task_id'], r['arm']) for r in reg['planned_order']]
    require([(r['task_id'], r['arm']) for r in rows] == expected and len(set(expected)) == len(expected), 'Assignment differs')
    require(all(r['status'] == 'returned' for r in rows), 'Unresolved cell; requires separate audit')
    tasks = {t['task_id']: t for t in reg['tasks']}
    known = set()
    outcomes = [audit_problem(root, r, tasks[r['task_id']], reg, known) for r in rows]
    require({Path(r['job']).name for r in rows} == {p.name for p in (root / 'jobs').glob(label + '-*') if p.is_dir()},
            'Missing or unregistered jobs')
    unit = subprocess.check_output(['systemctl', 'show', 'supergoal-context-handoff01.service',
                                    '-p', 'MainPID', '-p', 'ActiveState', '-p', 'Result', '-p', 'LoadState'], text=True)
    require('MainPID=0\n' in unit and 'ActiveState=inactive\n' in unit, 'Controller still running')
    result = {'status': 'passed', 'experiment': label, 'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'registration_sha256': sha(registration), 'receipt_sha256': sha(receipt_path),
              'audit_source_sha256': sha(Path(__file__)), 'unit': unit, 'source_files_verified': len(source_hashes),
              'task_trees_and_images_verified': len(tasks), 'unique_controllers': len(known),
              'new_model_calls': 0, 'rows': outcomes,
              'scope': 'Frozen identities, cumulative request ceilings and known grader-history restoration. '
                       'Actual SDK context activation and semantic outcomes are analyzed separately; failures remain unchanged.'}
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
