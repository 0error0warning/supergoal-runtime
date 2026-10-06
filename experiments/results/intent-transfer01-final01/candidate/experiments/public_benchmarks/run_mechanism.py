"""Finite registered study using the completed serial cohort's two CPUs.

The old six-CPU pool remains untouched. Admission begins only after the old
serial cohort ends. No speculative retries, no changed benchmark scores.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from run_registered import available_memory, check_environment, check_sources, now, summarize, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    root = Path('/var/lib/supergoal-lab')
    candidate = args.candidate
    label = candidate.name
    regpath = candidate / 'experiments/public_benchmarks' / f'registration-{label}.json'
    reg = json.loads(regpath.read_text())
    upstream = root / 'upstream/terminal-bench-2-1'
    check_sources(candidate, reg, upstream)
    environment = json.loads((candidate / 'experiments/public_benchmarks' /
                              reg.get('environment_lock_file', 'environment-mechanism01.json')).read_text())
    if environment['experiment'] != label:
        raise ValueError('Environment lock belongs to another experiment')
    check_environment(root, upstream, environment)
    if json.loads((root / 'setup' / reg['mini_integration_gate']).read_text())['status'] != 'passed':
        raise RuntimeError('Upstream mini integration gate failed')
    if json.loads((root / 'setup' / reg['pause_integration_gate']).read_text())['status'] != 'passed':
        raise RuntimeError('Actor pause / reviewer integration gate failed')
    for gate in reg.get('additional_integration_gates', []):
        if json.loads((root / 'setup' / gate).read_text())['status'] != 'passed':
            raise RuntimeError('Integration gate failed: ' + gate)
    receipt_path = root / 'setup' / f'{label}-receipt.json'
    if receipt_path.exists():
        raise FileExistsError('Never overwrite or retry a started study')
    receipt = {'status': 'waiting_reserved_capacity', 'started_at': now(), 'rows': [],
               'registration_sha256': hashlib.sha256(regpath.read_bytes()).hexdigest(),
               'cpu_limit': 2, 'memory_limit_mb': 12288, 'automatic_retries': 0}
    write_json(receipt_path, receipt)
    deadline = datetime.datetime.fromisoformat(reg['stop_admission_at_utc']).timestamp()
    # A running or failed old controller is not evidence its children stopped.
    while True:
        old = json.loads((root / 'setup' / reg.get('start_after_receipt', 'public-gcp01-rows-00-30.json')).read_text())
        if old['status'].startswith('complete') and all(r['status'] != 'running' for r in old['rows']):
            break
        if time.time() > deadline - 3600:
            receipt.update(status='not_started_capacity_unavailable', finished_at=now())
            write_json(receipt_path, receipt)
            return
        receipt['heartbeat_at'] = now()
        write_json(receipt_path, receipt)
        time.sleep(10)
    tasks = {t['task_id']: t for t in reg['tasks']}
    pending = [{'index': i, **row, 'status': 'pending'} for i, row in enumerate(reg['planned_order'])]
    receipt.update(status='running', rows=pending.copy(), capacity_opened_at=now())
    running, stopped = {}, False
    env = dict(os.environ, DOCKER_HOST='unix://' + str(root / 'run/docker.sock'),
               PYTHONPATH=os.pathsep.join(map(str, [candidate, candidate / 'experiments',
                                                  candidate / 'experiments/public_benchmarks'])))
    while pending or running:
        for row in list(pending):
            task = tasks[row['task_id']]
            if row['task_id'] in reg.get('environment_unavailable_tasks', []):
                row['status'] = 'environment_unavailable'
                pending.remove(row)
                continue
            if stopped or time.time() + task['agent_timeout_sec'] + task['verifier_timeout_sec'] + 600 > deadline:
                row['status'] = 'not_started_admission_closed'
                pending.remove(row)
                continue
            # All selected tasks have 1 CPU. During a Supergoal audit
            # the actor container is paused; two trials cannot exceed 2 CPUs.
            if task['resources']['cpus'] != 1:
                raise ValueError('This capacity registration supports only 1-CPU tasks')
            if len(running) >= 2:
                break
            if available_memory() < 10 * 1024**3 or shutil.disk_usage(root).free < reg.get('disk_reserve_gib', 40) * 1024**3:
                stopped = True
                receipt['stop_reason'] = 'host_reserve'
                break
            check_sources(candidate, reg, upstream)
            check_environment(root, upstream, environment)
            name = f"tb-{label}-{row['index']+1:03d}-{row['task_id']}-{row['arm']}"
            config_path = root / 'setup' / (name + '.json')
            job = root / 'jobs' / name
            if config_path.exists() or job.exists():
                raise FileExistsError('Started row requires audit: ' + name)
            config = {'job_name': name, 'jobs_dir': str(root / 'jobs'), 'n_concurrent_trials': 1,
                      'environment': {'type': 'docker', 'delete': True},
                      'agents': [{'import_path': reg.get('agent_import_paths', {}).get(row['arm']) or (
                                  'mini_agent:MiniSweStudy' if row['arm'] == 'mini_swe'
                                  else 'mechanism_agent:MechanismStudy'), 'model_name': 'devin/swe-2',
                                  'kwargs': {'arm': row['arm'], 'max_requests': reg['max_upstream_requests_per_trial'],
                                             'max_episodes': reg['max_outer_episodes'],
                                             'budget_seconds': task['agent_timeout_sec']}}],
                      'tasks': [{'path': str(upstream / 'tasks' / row['task_id'])}]}
            write_json(config_path, config)
            log = (root / 'setup' / (name + '.log')).open('xb')
            proc = subprocess.Popen([str(root / 'harbor-venv/bin/harbor'), 'run', '--config', str(config_path)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
            row.update(status='running', started_at=now(), job=str(job), harbor_pid=proc.pid)
            running[name] = (proc, log, row)
            pending.remove(row)
            write_json(receipt_path, receipt)
        for name, (proc, log, row) in list(running.items()):
            if proc.poll() is None:
                continue
            log.close()
            row.update(summarize(Path(row['job'])), exit_code=proc.returncode, finished_at=now())
            del running[name]
            if row['status'] not in {'graded', 'trial_exception'}:
                stopped = True
                receipt['stop_reason'] = 'verifier_apparatus_requires_audit'
        receipt['heartbeat_at'] = now()
        write_json(receipt_path, receipt)
        if pending or running:
            time.sleep(5)
    receipt.update(status='complete_with_itemized_outcomes', finished_at=now())
    write_json(receipt_path, receipt)
    subprocess.run([str(root / 'harbor-venv/bin/python'), str(candidate / 'experiments/public_benchmarks/finalize_registered.py'),
                    '--root', str(root), '--candidate', str(candidate), '--batch-receipt', str(receipt_path),
                    '--host', 'supergoal-gcp'], check=True)


if __name__ == '__main__':
    main()
