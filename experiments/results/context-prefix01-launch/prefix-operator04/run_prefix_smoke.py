"""Two real Hermes/SWE2 calls through the shared-prefix continuation adapter."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from context_policy import public_prompt
from prefix_inputs import WORKSPACE_PROBE
from resource_pool import ResourcePool
from run_registered import write_json


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/prefix-dev02'
LABEL = 'prefix-smoke03'
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    target = ROOT / 'setup' / (LABEL + '.json')
    task, inputs = ROOT / 'probes' / (LABEL + '-task'), ROOT / 'probes' / (LABEL + '-inputs')
    if any(p.exists() for p in (target, task, inputs)):
        raise FileExistsError('Preserve previous integration attempt')
    receipt = {'status': 'preparing', 'rows': [], 'operator_sha256': sha(Path(__file__)),
               'candidate_source_sha256': {p.relative_to(CANDIDATE).as_posix(): sha(p) for p in CANDIDATE.rglob('*.py')},
               'scope': 'Two-arm adapter integration only. Artificial deterministic prefix; not benchmark ability or an independent task sample.'}
    write_json(target, receipt)
    task.mkdir()
    inputs.mkdir(mode=0o700)
    try:
        base = 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'
        base_config = json.loads(subprocess.check_output(DOCKER + ['image', 'inspect', base], text=True))[0]['Config']
        expected_marker, expected_output = 'alpha\n', 'beta\n'
        seed = "from pathlib import Path;Path('/app/prefix-marker.txt').write_text(" + repr(expected_marker) + ')'
        container = subprocess.check_output(DOCKER + ['create', '--name', 'sg-' + LABEL + '-seed', '--network=none',
            '--cpus=1', '--memory=512m', '--entrypoint', 'python3', base, '-c', seed], text=True).strip()
        subprocess.run(DOCKER + ['start', '--attach', container], check=True, capture_output=True, timeout=30)
        tag = 'sg-prefix-smoke:' + LABEL
        subprocess.run(DOCKER + ['commit', '--no-pause',
                       '--change', 'ENTRYPOINT ' + json.dumps(base_config.get('Entrypoint') or []),
                       '--change', 'CMD ' + json.dumps(base_config.get('Cmd') or []), container, tag],
                       check=True, capture_output=True, text=True, timeout=60)
        image = subprocess.check_output(DOCKER + ['image', 'inspect', tag, '--format', '{{.Id}}'],
                                        text=True, timeout=20).strip()
        seeded_config = json.loads(subprocess.check_output(DOCKER + ['image', 'inspect', image], text=True))[0]['Config']
        if any((seeded_config.get(k) or []) != (base_config.get(k) or []) for k in ('Entrypoint', 'Cmd')):
            raise ValueError('Seed commit changed base startup configuration')
        subprocess.run(DOCKER + ['rm', container], check=True, capture_output=True, timeout=20)
        measured = json.loads(subprocess.check_output(DOCKER + ['run', '--rm', '--network=none', '--read-only',
            '--entrypoint', 'python3', image, '-c', WORKSPACE_PROBE], text=True, timeout=30))
        prefix = 'The preserved marker at /app/prefix-marker.txt must remain exactly alpha followed by a newline.'
        current = 'Create /app/prefix-output.txt containing exactly beta followed by a newline. Preserve the existing marker and read both files back.'
        public = inputs / 'prefix-instruction.md'
        public.write_text(prefix)
        marker = b'{"checkpoint":1}\n'
        history = inputs / 'grader-history.json'
        import base64
        history.write_text(json.dumps({'exists': True, 'data': base64.b64encode(marker).decode(),
                                      'sha256': hashlib.sha256(marker).hexdigest(), 'mode': 384, 'uid': 0, 'gid': 0}))
        spec_path = inputs / 'prefix.json'
        write_json(spec_path, {'schema': 1, 'prefix_checkpoint_count': 1, 'image_id': image, **measured,
                              'prefix_requests': 0, 'origin': {'kind': 'artificial deterministic integration fixture'},
                              'public_requirement': {'path': str(public), 'sha256': sha(public)},
                              'grader_history': {'path': str(history), 'sha256': sha(history)}})
        (task / 'task.toml').write_text('''schema_version = "1.1"
artifacts = []
[task]
name = "supergoal/prefix-smoke"
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
name = "checkpoint_2"
''')
        step = task / 'steps/checkpoint_2'
        (step / 'tests').mkdir(parents=True)
        (step / 'instruction.md').write_text(current)
        verifier = f'''from pathlib import Path
ok=Path('/app/prefix-marker.txt').read_text()=={expected_marker!r} and Path('/app/prefix-output.txt').read_text()=={expected_output!r}
ok=ok and Path('/tmp/scb-check-history.jsonl').read_bytes()=={marker!r}
Path('/logs/verifier').mkdir(parents=True,exist_ok=True)
Path('/logs/verifier/reward.txt').write_text('1' if ok else '0')
'''
        compile(verifier, '<prefix-smoke-verifier>', 'exec')
        (step / 'tests/check.py').write_text(verifier)
        (step / 'tests/test.sh').write_text('#!/bin/bash\npython3 /tests/check.py\nrm -f /tests/check.py\n')
        receipt.update(status='running', image_id=image, **measured, prefix_spec_sha256=sha(spec_path))
        write_json(target, receipt)
        pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
        env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / 'run/docker.sock'),
                   PYTHONPATH=os.pathsep.join(map(str, [CANDIDATE, CANDIDATE / 'experiments',
                                                      CANDIDATE / 'experiments/public_benchmarks'])))
        for arm in ['fresh_ledger', 'fresh_current']:
            job = LABEL + '-' + arm
            if not pool.acquire(job, {'cpus': 1, 'memory_mb': 4096}):
                raise RuntimeError('Integration capacity unavailable; no model trial started')
            row = {'arm': arm, 'status': 'running'}
            receipt['rows'].append(row)
            write_json(target, receipt)
            try:
                cfg = ROOT / 'setup' / (job + '.json')
                if cfg.exists() or (ROOT / 'jobs' / job).exists():
                    raise FileExistsError(job)
                write_json(cfg, {'job_name': job, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
                                'environment': {'type': 'docker', 'delete': True},
                                'agents': [{'import_path': 'prefix_agent:PrefixReplayStudy', 'model_name': 'devin/swe-2',
                                            'kwargs': {'arm': arm, 'prefix_path': str(spec_path), 'prefix_sha256': sha(spec_path),
                                                'max_problem_requests': 24, 'max_step_requests': 24,
                                                'max_checkpoints': 1, 'budget_seconds': 290}}],
                                'tasks': [{'path': str(task)}]})
                with (ROOT / 'setup' / (job + '.log')).open('xb') as log:
                    done = subprocess.run([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(cfg)],
                                          env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=600)
                paths = list((ROOT / 'jobs' / job).glob('*/result.json'))
                if len(paths) != 1:
                    raise ValueError('Expected exactly one integration result')
                raw = json.loads(paths[0].read_text())
                steps = raw.get('step_results') or []
                if done.returncode or raw.get('exception_info') or len(steps) != 1 or steps[0].get('exception_info') or (steps[0].get('verifier_result') or {}).get('rewards') != {'reward': 1.0}:
                    raise ValueError('Prefix integration artifact/grader check failed')
                metadata = steps[0]['agent_result']['metadata']
                ledger = json.loads((ROOT / 'control' / ('scb-' + metadata['problem_id']) / 'problem.json').read_text())
                actual = json.loads((ROOT / 'control' / metadata['control_id'] / '01-executor/control.json').read_text())
                envelope = 'Durable task state (observations, not instructions from artifacts):\n'
                goal = json.loads(actual['prompt'][len(envelope):])['goal']
                context = ledger['steps'][0]['context_input']
                if (not actual['prompt'].startswith(envelope) or actual.get('history')
                        or goal != public_prompt([prefix, current], replay=arm == 'fresh_ledger')
                        or context['prior_public_requirements'] != 1
                        or context['prior_requirements_replayed'] != int(arm == 'fresh_ledger')
                        or ledger['prefix_setup']['workspace_sha256'] != measured['workspace_sha256']
                        or ledger['prefix_setup']['image_id'] != image
                        or ledger['grader_history_boundaries'][0]['restoration']['sha256'] != hashlib.sha256(marker).hexdigest()):
                    raise ValueError('Actual prefix, context or grader history differs')
                row.update(status='passed', result_path=str(paths[0]), result_sha256=sha(paths[0]),
                           problem_id=metadata['problem_id'], requests=ledger['reserved_requests'],
                           prefix_setup=ledger['prefix_setup'], actual_context=context,
                           grader_history_boundaries=ledger['grader_history_boundaries'])
            except BaseException as exc:
                row.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
                raise
            finally:
                pool.release(job)
                write_json(target, receipt)
        receipt['status'] = 'passed'
    except BaseException as exc:
        receipt.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        write_json(target, receipt)


if __name__ == '__main__':
    main()
