"""Small frozen-cohort runner. Source/task pins and all raw failures are retained."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


ROOT = Path('/var/lib/supergoal-lab')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    temp.replace(path)


def verify_pins(root, pins):
    for name, expected in pins.items():
        if sha(root / name) != expected:
            raise ValueError('Frozen file changed: ' + name)


def run_row(reg, row):
    candidate = Path(reg['candidate'])
    task = next(t for t in reg['tasks'] if t['task_id'] == row['task_id'])
    verify_pins(candidate, reg['source_sha256'])
    verify_pins(Path(task['path']), task['files_sha256'])
    cutoff = datetime.datetime.fromisoformat(reg['absolute_stop']).timestamp()
    if time.time() + task['solver_seconds'] + task['verifier_seconds'] + 300 >= cutoff:
        return {**row, 'status': 'not_started_admission_closed'}
    if shutil.disk_usage(ROOT).free < 25 * 1024**3:
        return {**row, 'status': 'not_started_disk_reserve'}
    name = f"{reg['experiment']}-{row['index']:02d}-{row['task_id']}-{row['arm']}"
    cfg = ROOT / 'setup' / (name + '.json')
    job = ROOT / 'jobs' / name
    if cfg.exists() or job.exists():
        raise FileExistsError('Started rows cannot be overwritten or retried: ' + name)
    if row['arm'] == 'oracle':
        agents = [{'import_path': 'oracle_preflight:HttpsOracle',
                   'kwargs': {'task_dir': task['path'], 'agent_timeout_sec': task['solver_seconds']}}]
    else:
        agents = [{'import_path': 'evidence_agent:EvidenceStudy', 'model_name': 'devin/swe-2',
                   'kwargs': {'arm': row['arm'], 'max_requests': reg['max_requests'],
                              'max_episodes': reg['max_episodes'], 'budget_seconds': task['solver_seconds']}}]
    config = {'job_name': name, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
              'environment': {'type': 'docker', 'delete': True}, 'agents': agents,
              'tasks': [{'path': task['path']}]}
    write(cfg, config)
    env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
               PYTHONPATH=os.pathsep.join(map(str, [candidate, candidate / 'experiments',
                                                   candidate / 'experiments/public_benchmarks'])))
    result = {**row, 'status': 'running', 'job': str(job), 'started_at': now()}
    write(ROOT / 'setup' / (name + '-receipt.json'), result)
    with (ROOT / 'setup' / (name + '.log')).open('xb') as log:
        proc = subprocess.run([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(cfg)],
                              stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
    results = list(job.glob('*/result.json'))
    result.update(exit_code=proc.returncode, finished_at=now(), result_files=len(results))
    if len(results) == 1:
        data = json.loads(results[0].read_text())
        result.update(raw_rewards=(data.get('verifier_result') or {}).get('rewards'),
                      exception=data.get('exception_info'), result_path=str(results[0]),
                      agent_result=data.get('agent_result'))
        result['status'] = 'returned' if result['raw_rewards'] is not None else 'requires_audit'
    else:
        result['status'] = 'apparatus_incomplete'
    write(ROOT / 'setup' / (name + '-receipt.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('registration', type=Path)
    parser.add_argument('--oracles', action='store_true')
    args = parser.parse_args()
    reg = json.loads(args.registration.read_text())
    suffix = '-oracles' if args.oracles else ''
    path = ROOT / 'setup' / (reg['experiment'] + suffix + '-receipt.json')
    if path.exists():
        raise FileExistsError('Never automatically retry a started cohort')
    plan = ([{'index': i, 'task_id': t['task_id'], 'arm': 'oracle'} for i, t in enumerate(reg['tasks'])]
            if args.oracles else reg['planned_order'])
    if not args.oracles:
        reference = json.loads((ROOT / 'setup' / (reg['experiment'] + '-oracles-receipt.json')).read_text())
        eligible = {r['task_id'] for r in reference['rows']
                    if r.get('raw_rewards', {}).get('reward') == 1.0 and not r.get('exception')}
        if reference['status'] != 'complete':
            raise ValueError('Reference preflight unfinished')
    else:
        eligible = {t['task_id'] for t in reg['tasks']}
    report = {'status': 'running', 'started_at': now(), 'registration_sha256': sha(args.registration),
              'rows': [], 'automatic_retries': 0}
    write(path, report)
    with ThreadPoolExecutor(max_workers=reg['concurrency']) as executor:
        jobs = {}
        for row in plan:
            if row['task_id'] in eligible:
                jobs[executor.submit(run_row, reg, row)] = row
            else:
                report['rows'].append({**row, 'status': 'not_started_reference_unavailable'})
        for future in as_completed(jobs):
            try:
                report['rows'].append(future.result())
            except Exception as exc:
                report['rows'].append({**jobs[future], 'status': 'operator_exception',
                                       'error': type(exc).__name__ + ': ' + str(exc)})
            report['rows'].sort(key=lambda r: r['index'])
            write(path, report)
    report.update(status='complete', finished_at=now())
    write(path, report)


if __name__ == '__main__':
    main()
