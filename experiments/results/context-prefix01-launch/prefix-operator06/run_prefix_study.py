"""Six registered common-prefix suffixes; bounded admission, no trial retries."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from prefix_inputs import load_prefix
from resource_pool import ResourcePool
from run_registered import available_memory, write_json


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'context-prefix01'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    regpath = ROOT / 'setup' / ('registration-' + LABEL + '.json')
    reg = json.loads(regpath.read_text())
    candidate = Path(reg['candidate'])
    actual = {p.relative_to(candidate).as_posix(): sha(p) for p in candidate.rglob('*.py')}
    if actual != reg['source_sha256']:
        raise RuntimeError('Frozen candidate changed')
    for pinned in reg['pinned_files'].values():
        if sha(Path(pinned['path'])) != pinned['sha256']:
            raise RuntimeError('Frozen integration or reference gate changed')
    for task in reg['tasks']:
        directory = Path(task['path'])
        actual = {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob('*') if p.is_file()}
        if actual != task['files_sha256']:
            raise RuntimeError('Frozen task changed: ' + task['task_id'])
        load_prefix(task['prefix_spec'], task['prefix_spec_sha256'])
    target = ROOT / 'setup' / (LABEL + '-receipt.json')
    if target.exists():
        raise FileExistsError('Started cohort requires audit; never restart automatically')
    tasks = {t['task_id']: t for t in reg['tasks']}
    pending = [{'index': i, **row, 'status': 'pending'} for i, row in enumerate(reg['planned_order'])]
    receipt = {'status': 'running', 'rows': pending.copy(), 'automatic_retries': 0,
               'registration_sha256': sha(regpath)}
    write_json(target, receipt)
    deadline = datetime.datetime.fromisoformat(reg['stop_admission_at']).timestamp()
    pool, running = ResourcePool(ROOT / 'run/parallel-resource-pool.json'), {}
    env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
               PYTHONPATH=os.pathsep.join(map(str, [candidate, candidate / 'experiments',
                                                  candidate / 'experiments/public_benchmarks'])))
    while pending or running:
        for row in list(pending):
            task = tasks[row['task_id']]
            worst_seconds = task['checkpoints'] * (reg['step_solver_seconds'] + task['verifier_timeout_sec']) + 600
            if time.time() + worst_seconds > deadline:
                row['status'] = 'not_started_admission_closed'
                pending.remove(row)
                continue
            if len(running) >= reg['max_concurrent_problems']:
                break
            if available_memory() < 8 * 1024**3 or shutil.disk_usage(ROOT).free < reg['minimum_free_disk_gib'] * 1024**3:
                receipt['admission_status'] = 'waiting_host_reserve'
                break
            job = f"{LABEL}-{row['index']+1:02d}-{row['task_id']}-{row['arm']}"
            reservation = {'cpus': 1, 'memory_mb': 4096}
            if not pool.acquire(job, reservation):
                continue
            cfg = ROOT / 'setup' / (job + '.json')
            try:
                if cfg.exists() or (ROOT / 'jobs' / job).exists():
                    raise FileExistsError('Registered cell already started')
                load_prefix(task['prefix_spec'], task['prefix_spec_sha256'])
                config = {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                          'environment': {'type': 'docker', 'delete': True},
                          'agents': [{'import_path': 'prefix_agent:PrefixReplayStudy', 'model_name': reg['model'],
                                      'kwargs': {'arm': row['arm'], 'prefix_path': task['prefix_spec'],
                                          'prefix_sha256': task['prefix_spec_sha256'],
                                          'max_problem_requests': reg['max_problem_requests'],
                                          'max_step_requests': reg['max_step_requests'],
                                          'max_checkpoints': task['checkpoints'],
                                          'budget_seconds': reg['step_solver_seconds']}}],
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
            receipt['admission_status'] = 'admitting'
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
                row.update(result_path=str(paths[0]), result_sha256=sha(paths[0]),
                           raw_rewards=(raw.get('verifier_result') or {}).get('rewards'),
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
