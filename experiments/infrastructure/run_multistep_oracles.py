"""Finite official SCBench reference checks before any solver allocation."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib

from resource_pool import ResourcePool
from run_registered import write_json


ROOT = Path('/var/lib/supergoal-lab')


def main():
    source = ROOT / 'upstream/scb-harbor-20261006'
    pin = ROOT / 'setup/scb-public-source01.json'
    lock = json.loads(pin.read_text())
    if lock['status'] != 'downloaded':
        raise ValueError('Public source download incomplete')
    for name, expected in lock['files'].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Published task changed: ' + name)
    target = ROOT / 'setup/scb-oracle01.json'
    if target.exists():
        raise FileExistsError(target)
    receipt = {'status': 'waiting_resources', 'model_calls': 0,
               'source_lock_sha256': hashlib.sha256(pin.read_bytes()).hexdigest(),
               'operator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'scope': 'Official reference environment checks, not model success', 'rows': []}
    write_json(target, receipt)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    cutoff = datetime.datetime.fromisoformat('2026-10-06T12:14:06+00:00').timestamp()
    for name in ['file_backup', 'database_migration', 'dag_execution']:
        task = source / name
        metadata = tomllib.loads((task / 'task.toml').read_text())
        job = 'scb-oracle01-' + name
        while not pool.acquire(job, {'cpus': metadata['environment']['cpus'],
                                    'memory_mb': metadata['environment']['memory_mb']}):
            if time.time() > cutoff:
                receipt['status'] = 'admission_closed'
                write_json(target, receipt)
                return
            time.sleep(5)
        row = {'task': name, 'status': 'running', 'checkpoints': len(metadata['steps'])}
        receipt['rows'].append(row)
        receipt['status'] = 'running'
        write_json(target, receipt)
        try:
            if shutil.disk_usage(ROOT).free < 50 * 1024**3:
                raise RuntimeError('Disk reserve reached')
            config = {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                      'environment': {'type': 'docker', 'delete': False},
                      'agents': [{'name': 'oracle'}], 'tasks': [{'path': str(task)}]}
            path = ROOT / 'setup' / (job + '.json')
            if path.exists() or (ROOT / 'jobs' / job).exists():
                raise FileExistsError('Never overwrite a started reference run')
            write_json(path, config)
            env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'))
            with (ROOT / 'setup' / (job + '.log')).open('xb') as log:
                proc = subprocess.run([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(path)],
                                      env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
            results = list((ROOT / 'jobs' / job).glob('*/result.json'))
            row['exit_code'] = proc.returncode
            if len(results) != 1:
                raise RuntimeError('Expected exactly one reference trial result')
            raw = json.loads(results[0].read_text())
            row.update(result_path=str(results[0]), raw_rewards=(raw.get('verifier_result') or {}).get('rewards'),
                       exception=raw.get('exception_info'), steps=raw.get('step_results'))
            steps = raw.get('step_results') or []
            valid = (proc.returncode == 0 and len(steps) == len(metadata['steps'])
                     and all(not s.get('exception_info') and (s.get('verifier_result') or {}).get('rewards')
                             for s in steps))
            perfect = valid and all(s['verifier_result']['rewards'].get('strict_pass_rate') == 1 for s in steps)
            row['status'] = 'reference_passed' if perfect else 'reference_requires_audit'
            if not perfect:
                receipt['status'] = 'stopped_for_audit'
                write_json(target, receipt)
                return
        except BaseException as exc:
            row.update(status='apparatus_failure', error=type(exc).__name__ + ': ' + str(exc))
            receipt['status'] = 'stopped_for_audit'
            write_json(target, receipt)
            raise
        finally:
            row['finished_epoch'] = time.time()
            pool.release(job)
            write_json(target, receipt)
    receipt['status'] = 'complete'
    write_json(target, receipt)


if __name__ == '__main__':
    main()

