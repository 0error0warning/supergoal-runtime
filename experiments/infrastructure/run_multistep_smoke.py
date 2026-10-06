"""Real Harbor two-checkpoint lifecycle for all three intended solver arms."""
import json
import os
from pathlib import Path
import subprocess
import time

from resource_pool import ResourcePool
from run_registered import write_json


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/scb-dev02'


def main():
    target = ROOT / 'setup/scb-multistep-smoke02.json'
    if target.exists():
        raise FileExistsError(target)
    task = ROOT / 'probes/scb-multistep-smoke02'
    task.mkdir(exist_ok=False)
    image = 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'
    (task / 'task.toml').write_text('''schema_version = "1.1"
artifacts = []
[task]
name = "supergoal/multistep-smoke"
[environment]
docker_image = "''' + image + '''"
cpus = 1
memory_mb = 2048
storage_mb = 10240
allow_internet = false
workdir = "/app"
[agent]
timeout_sec = 240.0
[verifier]
timeout_sec = 30.0
[[steps]]
name = "checkpoint_1"
[[steps]]
name = "checkpoint_2"
''')
    prompts = [
        'Create /app/horizon-smoke.txt containing exactly alpha followed by a newline. Read it back and finish.',
        'Extend the existing /app/horizon-smoke.txt by appending beta followed by a newline. '
        'The required final content is now exactly two lines: alpha then beta. Read it back and finish.',
    ]
    for i, prompt in enumerate(prompts, 1):
        step = task / 'steps' / f'checkpoint_{i}'
        (step / 'tests').mkdir(parents=True)
        (step / 'instruction.md').write_text(prompt)
        expected = 'alpha\\n' if i == 1 else 'alpha\\nbeta\\n'
        (step / 'tests/test.sh').write_text(
            '#!/bin/bash\nmkdir -p /logs/verifier\n'
            "printf 'PRIVATE_GRADER_MARKER' > /tests/SG_HIDDEN_VERIFIER_CANARY\n"
            f"if printf '{expected}' | cmp - /app/horizon-smoke.txt; then echo 1; else echo 0; fi > /logs/verifier/reward.txt\n")
    receipt = {'status': 'waiting_resources', 'rows': [], 'base_image': image,
               'scope': 'Integration probe only, excluded from benchmark capability sample'}
    write_json(target, receipt)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    for arm in ['native', 'sg_v2', 'mini_swe']:
        job = 'scb-multistep-smoke02-' + arm
        started = time.monotonic()
        while not pool.acquire(job, {'cpus': 1, 'memory_mb': 4096}):
            if time.monotonic() - started > 3600:
                raise TimeoutError('Probe capacity admission expired')
            time.sleep(5)
        row = {'arm': arm, 'status': 'running'}
        receipt['rows'].append(row)
        receipt['status'] = 'running'
        write_json(target, receipt)
        try:
            config = {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                      'environment': {'type': 'docker', 'delete': False},
                      'agents': [{'import_path': 'multistep_smoke_agent:CheckedMultiStep',
                                  'model_name': 'devin/swe-2', 'kwargs': {'arm': arm, 'max_problem_requests': 16,
                                      'max_step_requests': 10, 'max_checkpoints': 2, 'budget_seconds': 230}}],
                      'tasks': [{'path': str(task)}]}
            cfg = ROOT / 'setup' / (job + '.json')
            if cfg.exists() or (ROOT / 'jobs' / job).exists():
                raise FileExistsError('Do not overwrite prior probe')
            write_json(cfg, config)
            env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
                       PYTHONPATH=os.pathsep.join(map(str, [CANDIDATE, CANDIDATE / 'experiments',
                           CANDIDATE / 'experiments/public_benchmarks', CANDIDATE / 'experiments/infrastructure'])))
            with (ROOT / 'setup' / (job + '.log')).open('xb') as log:
                proc = subprocess.run([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(cfg)],
                                      env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            result_paths = list((ROOT / 'jobs' / job).glob('*/result.json'))
            if len(result_paths) != 1:
                raise RuntimeError('Expected one probe result')
            raw = json.loads(result_paths[0].read_text())
            steps = raw.get('step_results') or []
            metadata = [(s.get('agent_result') or {}).get('metadata') or {} for s in steps]
            valid = (proc.returncode == 0 and len(steps) == 2 and all(
                not s.get('exception_info') and (s.get('verifier_result') or {}).get('rewards') == {'reward': 1.0}
                for s in steps) and all(m.get('previous_hidden_verifier_absent') for m in metadata)
                and metadata[0].get('control_id') != metadata[1].get('control_id')
                and 0 < metadata[0].get('problem_requests_total', 0) < metadata[1].get('problem_requests_total', 0) <= 16)
            row.update(status='passed' if valid else 'failed', result_path=str(result_paths[0]), steps=steps,
                       exception=raw.get('exception_info'))
            if not valid:
                receipt['status'] = 'failed'
                return
        except BaseException as exc:
            row.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
            receipt['status'] = 'failed'
            raise
        finally:
            pool.release(job)
            write_json(target, receipt)
    receipt['status'] = 'passed'
    write_json(target, receipt)


if __name__ == '__main__':
    main()
