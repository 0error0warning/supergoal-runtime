"""Registered 2x2 context study; shared capacity, no model-trial retries."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from resource_pool import ResourcePool
from run_registered import available_memory, write_json


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/context-handoff01'


def main():
    regpath = ROOT / 'setup/registration-context-handoff01.json'
    reg = json.loads(regpath.read_text())
    for name, expected in reg['source_sha256'].items():
        if hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Frozen candidate changed: ' + name)
    for task in reg['tasks']:
        for name, expected in task['files_sha256'].items():
            if hashlib.sha256((Path(task['path']) / name).read_bytes()).hexdigest() != expected:
                raise RuntimeError('Frozen task changed: ' + task['task_id'] + '/' + name)
    target = ROOT / 'setup/context-handoff01-receipt.json'
    if target.exists():
        raise FileExistsError('A started cohort requires audit, never automatic restart')
    receipt = {'status': 'waiting_integration_gate', 'rows': [], 'automatic_retries': 0,
               'registration_sha256': hashlib.sha256(regpath.read_bytes()).hexdigest()}
    write_json(target, receipt)
    deadline = datetime.datetime.fromisoformat(reg['stop_admission_at']).timestamp()
    while True:
        gate = json.loads((ROOT / 'setup/context-smoke01.json').read_text())
        if gate['status'] == 'passed':
            break
        if gate['status'] == 'failed' or time.time() > deadline - 36600:
            receipt['status'] = 'not_started_integration_gate_failed_or_expired'
            write_json(target, receipt)
            return
        receipt['heartbeat_epoch'] = time.time()
        write_json(target, receipt)
        time.sleep(5)
    if json.loads((ROOT / 'setup/scb-oracle01.json').read_text())['status'] != 'complete':
        raise RuntimeError('Official reference checks incomplete')
    if json.loads((ROOT / 'setup/compose-retention-probe04.json').read_text())['status'] != 'passed':
        raise RuntimeError('Checkpoint lifecycle integration gate failed')
    tasks = {t['task_id']: t for t in reg['tasks']}
    pending = [{'index': i, **row, 'status': 'pending'} for i, row in enumerate(reg['planned_order'])]
    receipt.update(status='running', rows=pending.copy())
    write_json(target, receipt)
    pool, running = ResourcePool(ROOT / 'run/parallel-resource-pool.json'), {}
    env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
               PYTHONPATH=os.pathsep.join(map(str, [CANDIDATE, CANDIDATE / 'experiments',
                                                  CANDIDATE / 'experiments/public_benchmarks'])))
    while pending or running:
        for row in list(pending):
            task = tasks[row['task_id']]
            worst_seconds = task['checkpoints'] * (1800 + task['verifier_timeout_sec']) + 600
            if time.time() + worst_seconds > deadline:
                row['status'] = 'not_started_admission_closed'
                pending.remove(row)
                continue
            if len(running) >= 6:
                break
            if available_memory() < 8 * 1024**3 or shutil.disk_usage(ROOT).free < 60 * 1024**3:
                receipt['admission_status'] = 'waiting_host_reserve'
                break
            job = f"context-handoff01-{row['index']+1:02d}-{row['task_id']}-{row['arm']}"
            reservation = {'cpus': 1, 'memory_mb': 4096}
            if not pool.acquire(job, reservation):
                continue
            cfg = ROOT / 'setup' / (job + '.json')
            try:
                if cfg.exists() or (ROOT / 'jobs' / job).exists():
                    raise FileExistsError('Registered cell already started')
                config = {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                          'environment': {'type': 'docker', 'delete': True},
                          'agents': [{'import_path': 'context_multistep_agent:ContextMultiStepStudy', 'model_name': 'devin/swe-2',
                                      'kwargs': {'arm': row['arm'], 'max_problem_requests': 384,
                                          'max_step_requests': 128, 'max_checkpoints': task['checkpoints'],
                                          'budget_seconds': 1800}}],
                          'tasks': [{'path': task['path']}]}
                write_json(cfg, config)
                log = (ROOT / 'setup' / (job + '.log')).open('xb')
                proc = subprocess.Popen([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(cfg)],
                                        env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            except BaseException:
                pool.release(job)
                raise
            row.update(status='running', started_epoch=time.time(), job=str(ROOT / 'jobs' / job),
                       harbor_pid=proc.pid, reservation=reservation)
            running[job] = (proc, log, row)
            pending.remove(row)
            write_json(target, receipt)
        for job, (proc, log, row) in list(running.items()):
            if proc.poll() is None:
                continue
            log.close()
            paths = list((ROOT / 'jobs' / job).glob('*/result.json'))
            row.update(exit_code=proc.returncode, finished_epoch=time.time())
            if len(paths) != 1:
                row['status'] = 'apparatus_incomplete'
            else:
                raw = json.loads(paths[0].read_text())
                row.update(result_path=str(paths[0]), raw_rewards=(raw.get('verifier_result') or {}).get('rewards'),
                           exception=raw.get('exception_info'), steps=raw.get('step_results'))
                row['status'] = 'returned' if row['raw_rewards'] is not None else 'requires_audit'
            pool.release(job)
            del running[job]
        receipt['heartbeat_epoch'] = time.time()
        write_json(target, receipt)
        if pending or running:
            time.sleep(5)
    receipt['status'] = 'complete_with_itemized_outcomes'
    write_json(target, receipt)


if __name__ == '__main__':
    main()
