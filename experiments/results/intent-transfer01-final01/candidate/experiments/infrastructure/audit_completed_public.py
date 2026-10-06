"""Read-only final identity and budget audit; never run or regrade a task."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/var/lib/supergoal-lab'))
    parser.add_argument('--experiment', required=True)
    parser.add_argument('--operator', type=Path, required=True)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Preserve previous audits')
    root = args.root.resolve()
    candidate = root / 'candidates' / args.experiment
    reg_path = candidate / 'experiments/public_benchmarks' / f'registration-{args.experiment}.json'
    reg = json.loads(reg_path.read_text())
    receipt = json.loads(args.receipt.read_text())
    environment = json.loads(args.environment.read_text())
    require(receipt['status'].startswith('complete'), 'Batch is still unfinished')
    require(sha(reg_path) == receipt['registration_sha256'], 'Registration changed')
    require(sha(args.environment) == receipt['environment_lock_sha256'], 'Environment lock changed')
    for name, expected in receipt['operator_sha256'].items():
        require(sha(args.operator / name) == expected, 'Operator changed: ' + name)
    # Reuse the checks used at admission, loaded from the recorded operator.
    sys.path.insert(0, str(args.operator))
    spec = importlib.util.spec_from_file_location('frozen_checks', args.operator / 'run_registered.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    upstream = root / 'upstream/terminal-bench-2-1'
    checks.check_sources(candidate, reg, upstream)
    checks.check_environment(root, upstream, environment)
    commit = subprocess.check_output(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    require(commit == reg['official_source_commit'], 'Official source commit changed')

    rows = receipt['rows']
    expected = [(x['task_id'], x['arm']) for x in reg['planned_order']]
    actual = [(x['task_id'], x['arm']) for x in rows]
    require(actual == expected and len(set(actual)) == len(actual), 'Assigned cells differ or repeat')
    require(receipt['automatic_trial_retries'] == 0, 'Unexpected retry policy')
    controllers, jobs, audited = set(), set(), []
    for row in rows:
        key = (row['task_id'], row['arm'])
        if row['status'] == 'environment_unavailable':
            require(not row.get('job') and not row.get('agent_metadata'), 'Unavailable row ran a model')
            audited.append({'task_id': key[0], 'arm': key[1], 'status': row['status']})
            continue
        require(row['status'] in {'graded', 'trial_exception'}, 'Unresolved row: ' + str(key))
        job = Path(row['job']).resolve()
        result_path = Path(row['result_path']).resolve()
        require(job.is_relative_to(root / 'jobs') and result_path.is_relative_to(job), 'Result path escaped')
        require(job not in jobs, 'Duplicate job')
        jobs.add(job)
        result = json.loads(result_path.read_text())
        metadata = (result.get('agent_result') or {}).get('metadata') or {}
        uid = metadata.get('control_id', '')
        require(re.fullmatch(r'[a-f0-9]{32}', uid) and uid not in controllers, 'Invalid or reused controller')
        controllers.add(uid)
        require(metadata['study_arm'] == key[1] and result['task_name'].removeprefix('terminal-bench/') == key[0], 'Cell identity mismatch')
        report_path = root / 'control' / uid / 'report.json'
        report = json.loads(report_path.read_text())
        require(report['control_id'] == uid and report['arm'] == key[1], 'Report identity mismatch')
        used = report['used_requests']
        require(isinstance(used, int) and 0 <= used <= reg['max_upstream_requests_per_trial'], 'Request limit exceeded')
        require(metadata['physical_requests'] == used, 'Request counts disagree')
        requests = report.get('requests', [])
        require(len(requests) <= used, 'More request entries than charged reservations')
        models = Counter(r.get('request_model') for r in requests)
        require(set(models) <= {'devin/swe-2'}, 'Unexpected model')
        audited.append({'task_id': key[0], 'arm': key[1], 'status': row['status'], 'control_id': uid,
                        'result_sha256': sha(result_path), 'report_sha256': sha(report_path),
                        'used_requests': used, 'recorded_request_entries': len(requests),
                        'request_models': dict(models),
                        'official_raw_rewards': (result.get('verifier_result') or {}).get('rewards'),
                        'exception_type': (result.get('exception_info') or {}).get('exception_type')})
    expected_jobs = {Path(x['job']).name for x in rows if x.get('job')}
    observed_jobs = {p.name for p in (root / 'jobs').glob(f'tb-{args.experiment}-*') if p.is_dir()}
    require(expected_jobs == observed_jobs, 'Unexpected or missing jobs for registered experiment')
    state = subprocess.check_output(['systemctl', 'show', f'supergoal-{args.experiment}-run.service',
                                     '-p', 'MainPID', '-p', 'ActiveState', '-p', 'Result', '-p', 'LoadState'], text=True)
    require('MainPID=0\n' in state and 'ActiveState=inactive\n' in state, 'Batch service is still live')
    output = {'status': 'passed', 'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'Frozen identity, unique assigned cells, request accounting and recorded limit; does not certify semantic acceptance or repair observed failures.',
              'experiment': args.experiment, 'registration_sha256': sha(reg_path),
              'registered_sources_verified': len(reg['source_sha256']),
              'official_task_trees_verified': len(environment['tasks']), 'official_source_commit': commit,
              'environment_lock_sha256': sha(args.environment), 'receipt_sha256': sha(args.receipt),
              'audit_source_sha256': sha(Path(__file__)), 'unit_state': state,
              'row_status_counts': dict(Counter(r['status'] for r in rows)),
              'unique_model_controllers': len(controllers), 'new_model_calls': 0, 'new_trials_or_retries': 0,
              'rows': audited}
    with args.output.open('x') as stream:
        json.dump(output, stream, indent=2)
    print(json.dumps({k: v for k, v in output.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
