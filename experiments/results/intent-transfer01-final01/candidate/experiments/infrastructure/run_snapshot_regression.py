"""One registered real CompCert handoff regression; no best-of-N replacement."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from resource_pool import ResourcePool
from run_registered import summarize, write_json


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/checkpoint-dev07'


def main():
    label = 'checkpoint-handoff01'
    registration = ROOT / 'setup/registration-checkpoint-handoff01.json'
    reg = json.loads(registration.read_text())
    for name, expected in reg['source_sha256'].items():
        if hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Frozen source changed: ' + name)
    if json.loads((ROOT / 'setup/snapshot-lifecycle-probe02.json').read_text())['status'] != 'passed':
        raise RuntimeError('Actual timeout/cancellation probe must pass')
    receipt_path = ROOT / 'setup' / (label + '-receipt.json')
    if receipt_path.exists():
        raise FileExistsError(receipt_path)
    receipt = {'status': 'waiting_resources', 'model_calls_before_admission': 0,
               'registration_sha256': hashlib.sha256(registration.read_bytes()).hexdigest(),
               'original_failed_trials_unchanged': True}
    write_json(receipt_path, receipt)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    cutoff = datetime.datetime.fromisoformat(reg['stop_admission_at']).timestamp()
    while not pool.acquire(label, {'cpus': 2, 'memory_mb': 4096}):
        if time.time() + 5400 > cutoff:
            receipt['status'] = 'not_started_admission_closed'
            write_json(receipt_path, receipt)
            return
        time.sleep(5)
    try:
        if shutil.disk_usage(ROOT).free < 40 * 1024**3:
            raise RuntimeError('Insufficient disk reserve')
        job = 'tb-' + label + '-001-compile-compcert-native'
        config_path = ROOT / 'setup' / (job + '.json')
        if config_path.exists() or (ROOT / 'jobs' / job).exists():
            raise FileExistsError('Never repeat a started regression')
        config = {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                  'environment': {'type': 'docker', 'delete': True},
                  'agents': [{'import_path': 'checkpoint_agent:CheckpointStudy', 'model_name': 'devin/swe-2',
                              'kwargs': {'arm': 'native', 'max_requests': 192, 'max_episodes': 1,
                                         'budget_seconds': 2400}}],
                  'tasks': [{'path': str(ROOT / 'upstream/terminal-bench-2-1/tasks/compile-compcert')}]}
        write_json(config_path, config)
        receipt.update(status='running', started_epoch=time.time(), job=str(ROOT / 'jobs' / job))
        write_json(receipt_path, receipt)
        env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
                   PYTHONPATH=os.pathsep.join(map(str, [CANDIDATE, CANDIDATE / 'experiments',
                                                      CANDIDATE / 'experiments/public_benchmarks'])))
        with (ROOT / 'setup' / (job + '.log')).open('xb') as log:
            result = subprocess.run([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(config_path)],
                                    env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        receipt.update(summarize(ROOT / 'jobs' / job), exit_code=result.returncode,
                       finished_epoch=time.time())
        uid = (receipt.get('agent_metadata') or {}).get('control_id')
        if uid:
            report = json.loads((ROOT / 'control' / uid / 'report.json').read_text())
            receipt['snapshot_lifecycle'] = report.get('snapshot_lifecycle')
            receipt['artifact_snapshot_error'] = report.get('artifact_snapshot_error')
        write_json(receipt_path, receipt)
    except BaseException as exc:
        receipt.update(status='requires_audit', error=type(exc).__name__ + ': ' + str(exc))
        write_json(receipt_path, receipt)
        raise
    finally:
        pool.release(label)


if __name__ == '__main__':
    main()

