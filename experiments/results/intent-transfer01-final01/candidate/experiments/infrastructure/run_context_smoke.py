"""Eight real checkpoint calls: verify context payloads and grader restoration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from context_policy import CONDITIONS, fingerprint
from resource_pool import ResourcePool
from run_registered import write_json


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/context-dev01'
LABEL = 'context-smoke01'


def main():
    target = ROOT / 'setup' / (LABEL + '.json')
    if target.exists():
        raise FileExistsError(target)
    task = ROOT / 'probes' / LABEL
    task.mkdir(exist_ok=False)
    image = 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'
    (task / 'task.toml').write_text('''schema_version = "1.1"
artifacts = []
[task]
name = "supergoal/context-smoke"
[environment]
docker_image = "''' + image + '''"
cpus = 1
memory_mb = 2048
storage_mb = 10240
allow_internet = false
workdir = "/app"
[agent]
timeout_sec = 300.0
[verifier]
timeout_sec = 30.0
[[steps]]
name = "checkpoint_1"
[[steps]]
name = "checkpoint_2"
''')
    instructions = ['Create /app/context-smoke.txt containing exactly alpha followed by a newline. Read it back and finish.',
                    'Append beta followed by a newline to the file created in the previous checkpoint. Preserve its previous contents. Read it back and finish.']
    for index, prompt in enumerate(instructions, 1):
        step = task / 'steps' / f'checkpoint_{index}'
        (step / 'tests').mkdir(parents=True)
        (step / 'instruction.md').write_text(prompt)
        expected = 'alpha\n' if index == 1 else 'alpha\nbeta\n'
        marker = b'{"checkpoint":1}\n'
        script = f'''from pathlib import Path
p=Path('/tmp/scb-check-history.jsonl')
expected={expected!r}
file_ok=Path('/app/context-smoke.txt').read_text()==expected
history_ok=(not p.exists()) if {index}==1 else p.read_bytes()=={marker!r}
Path('/logs/verifier').mkdir(exist_ok=True,parents=True)
Path('/logs/verifier/reward.txt').write_text('1' if file_ok and history_ok else '0')
p.write_bytes({marker!r})
'''
        compile(script, '<context-smoke-verifier>', 'exec')
        (step / 'tests/check.py').write_text(script)
        (step / 'tests/test.sh').write_text('#!/bin/bash\npython3 /tests/check.py\nrm -f /tests/check.py\n')
    sources = {p.relative_to(CANDIDATE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(CANDIDATE.rglob('*.py'))}
    receipt = {'status': 'running', 'rows': [], 'candidate_source_sha256': sources,
               'scope': 'Integration and intervention activation only; no benchmark capability inference'}
    write_json(target, receipt)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
               PYTHONPATH=os.pathsep.join(map(str, [CANDIDATE, CANDIDATE / 'experiments',
                                                   CANDIDATE / 'experiments/public_benchmarks'])))
    for arm, (carry, replay) in CONDITIONS.items():
        job = LABEL + '-' + arm
        started = time.monotonic()
        while not pool.acquire(job, {'cpus': 1, 'memory_mb': 4096}):
            if time.monotonic() - started > 600:
                raise TimeoutError('Integration capacity unavailable')
            time.sleep(5)
        row = {'arm': arm, 'status': 'running'}
        receipt['rows'].append(row)
        write_json(target, receipt)
        try:
            cfg = ROOT / 'setup' / (job + '.json')
            if cfg.exists() or (ROOT / 'jobs' / job).exists():
                raise FileExistsError(job)
            write_json(cfg, {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                             'environment': {'type': 'docker', 'delete': True},
                             'agents': [{'import_path': 'context_multistep_agent:ContextMultiStepStudy',
                                         'model_name': 'devin/swe-2', 'kwargs': {'arm': arm, 'max_problem_requests': 24,
                                         'max_step_requests': 12, 'max_checkpoints': 2, 'budget_seconds': 290}}],
                             'tasks': [{'path': str(task)}]})
            with (ROOT / 'setup' / (job + '.log')).open('xb') as log:
                done = subprocess.run([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(cfg)],
                                      env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=900)
            result_paths = list((ROOT / 'jobs' / job).glob('*/result.json'))
            if len(result_paths) != 1:
                raise ValueError('Expected one integration result')
            raw = json.loads(result_paths[0].read_text())
            steps = raw.get('step_results') or []
            if done.returncode or len(steps) != 2 or any(s.get('exception_info') or
                    (s.get('verifier_result') or {}).get('rewards') != {'reward': 1.0} for s in steps):
                raise ValueError('Integration task or grader restoration did not pass')
            metadata = [s['agent_result']['metadata'] for s in steps]
            problem = metadata[0]['problem_id']
            ledger = json.loads((ROOT / 'control' / ('scb-' + problem) / 'problem.json').read_text())
            second = ledger['steps'][1]
            incoming = second['context_input']
            if (incoming['prior_requirements_replayed'] != int(replay)
                    or bool(incoming['incoming_history_messages']) != carry
                    or not all(x.get('restoration', {}).get('status') == 'restored'
                               for x in ledger['grader_history_boundaries'])):
                raise ValueError('Intervention or grader isolation was not exercised')
            child = ROOT / 'control' / second['control_id']
            payload = json.loads((child / '01-executor/control.json').read_text())
            observed = payload.get('history')
            if ((fingerprint(observed) if observed else None) != incoming['incoming_history_sha256']
                    or bool(instructions[0] in payload['prompt']) != replay):
                raise ValueError('Actual Hermes payload differs from context receipt')
            if not 0 < ledger['reserved_requests'] <= 24:
                raise ValueError('Problem request budget mismatch')
            row.update(status='passed', result_path=str(result_paths[0]), problem_id=problem,
                       requests=ledger['reserved_requests'], second_checkpoint_input=incoming,
                       grader_history_boundaries=ledger['grader_history_boundaries'])
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
