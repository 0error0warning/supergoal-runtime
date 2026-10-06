"""One finite, zero-model reference check of each prepared common-prefix suffix."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from resource_pool import ResourcePool
from run_registered import write_json


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/prefix-dev01'
LABEL = 'prefix-oracle01'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    inputs_path = ROOT / 'setup/context-prefix01-inputs.json'
    inputs = json.loads(inputs_path.read_text())
    target = ROOT / 'setup' / (LABEL + '.json')
    if target.exists():
        raise FileExistsError('Preserve existing reference attempt')
    source = {p.relative_to(CANDIDATE).as_posix(): sha(p) for p in CANDIDATE.rglob('*.py')}
    receipt = {'status': 'running', 'rows': [], 'model_calls': 0, 'automatic_retries': 0,
               'inputs_sha256': sha(inputs_path), 'candidate_source_sha256': source,
               'operator_sha256': sha(Path(__file__)),
               'scope': 'Original Harbor oracle and official suffix tests with exactly the fixed prefix setup; not model capability results'}
    write_json(target, receipt)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
               PYTHONPATH=os.pathsep.join(map(str, [CANDIDATE, CANDIDATE / 'experiments',
                                                  CANDIDATE / 'experiments/public_benchmarks'])))
    pending, running = list(inputs['tasks']), {}
    while pending or running:
        for task in list(pending):
            job = LABEL + '-' + task['task_id']
            if not pool.acquire(job, {'cpus': 1, 'memory_mb': 4096}):
                continue
            cfg = ROOT / 'setup' / (job + '.json')
            try:
                if cfg.exists() or (ROOT / 'jobs' / job).exists():
                    raise FileExistsError('Reference job already exists')
                write_json(cfg, {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                                'environment': {'type': 'docker', 'delete': True},
                                'agents': [{'import_path': 'prefix_agent:PrefixOracleAgent',
                                            'kwargs': {'prefix_path': task['prefix_spec'],
                                                       'prefix_sha256': task['prefix_spec_sha256']}}],
                                'tasks': [{'path': task['path']}]})
                log = (ROOT / 'setup' / (job + '.log')).open('xb')
                process = subprocess.Popen([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(cfg)],
                                           env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            except BaseException:
                pool.release(job)
                raise
            row = {'task_id': task['task_id'], 'status': 'running', 'job': str(ROOT / 'jobs' / job),
                   'harbor_pid': process.pid, 'started_epoch': time.time(), 'expected_checkpoints': task['checkpoints']}
            receipt['rows'].append(row)
            running[job] = (process, log, row)
            pending.remove(task)
            write_json(target, receipt)
        for job, (process, log, row) in list(running.items()):
            if process.poll() is None:
                continue
            log.close()
            row.update(exit_code=process.returncode, finished_epoch=time.time())
            paths = list((ROOT / 'jobs' / job).glob('*/result.json'))
            if len(paths) != 1:
                row['status'] = 'missing_or_duplicate_result'
            else:
                raw = json.loads(paths[0].read_text())
                steps = raw.get('step_results') or []
                row.update(result_path=str(paths[0]), result_sha256=sha(paths[0]),
                           raw_rewards=(raw.get('verifier_result') or {}).get('rewards'),
                           exception=raw.get('exception_info'), steps=steps)
                valid = process.returncode == 0 and not row['exception'] and len(steps) == row['expected_checkpoints']
                valid = valid and all(not s.get('exception_info') and
                    ((s.get('verifier_result') or {}).get('rewards') or {}).get('strict_pass_rate') == 1 for s in steps)
                row['status'] = 'reference_passed' if valid else 'reference_failed'
            pool.release(job)
            del running[job]
        receipt['heartbeat_epoch'] = time.time()
        write_json(target, receipt)
        if pending or running:
            time.sleep(5)
    receipt['status'] = 'passed' if len(receipt['rows']) == 3 and all(r['status'] == 'reference_passed' for r in receipt['rows']) else 'failed'
    write_json(target, receipt)


if __name__ == '__main__':
    main()
