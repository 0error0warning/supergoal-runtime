"""Freeze new public tasks and candidate before any transfer solver call."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib


ROOT = Path('/var/lib/supergoal-lab')
TASKS = [
    ('file_backup', 4, 600, 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'),
    ('database_migration', 5, 1800, 'sha256:76e49872bd74a989df7737f813d1d88cf55a2470cdc0025dfa55bf8e53b8e65c'),
    ('dag_execution', 3, 1800, 'sha256:8e3c3a77548bd1a0b2e19bca8e13ea9ecb9d6d9117ad45013ae2326e350acbc9'),
]


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root):
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Unpinned symlink in task tree')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_hash(path)
    return files


def adapt_config(original, image, checkpoints):
    adapted, count = re.subn(r'(\[environment\]\n)', r'\g<1>docker_image = "' + image + '"\n', original)
    if count != 1:
        raise ValueError('Expected one environment definition')
    adapted, count = re.subn(r'(\[(?:steps\.)?agent\]\n)timeout_sec = 7200\.0',
                            r'\g<1>timeout_sec = 1800.0', adapted)
    if count != checkpoints + 1:
        raise ValueError('Unexpected upstream agent timeout schema')
    before, after = tomllib.loads(original), tomllib.loads(adapted)
    # This structural check prevents a text substitution from changing grading.
    before['environment']['docker_image'] = image
    before['agent']['timeout_sec'] = 1800.0
    for step in before['steps']:
        step['agent']['timeout_sec'] = 1800.0
    if before != after:
        raise ValueError('Undeclared task adaptation')
    return adapted


def main():
    candidate = ROOT / 'candidates/scb-transfer01'
    task_root = ROOT / 'tasks/scb-transfer01'
    regpath = ROOT / 'setup/registration-scb-transfer01.json'
    if candidate.exists() or task_root.exists() or regpath.exists():
        raise FileExistsError('Do not replace a frozen cohort')
    source = ROOT / 'candidates/scb-dev02'
    source_lock = json.loads((ROOT / 'setup/scb-dev02-source.json').read_text())
    for name, expected in source_lock['source_sha256'].items():
        if file_hash(source / name) != expected:
            raise ValueError('Integration candidate changed: ' + name)
    if json.loads((ROOT / 'setup/scb-oracle01.json').read_text())['status'] != 'complete':
        raise ValueError('Reference environment checks incomplete')
    mini_commit = subprocess.check_output(['git', '-C', str(ROOT / 'upstream/mini-swe-agent'),
                                          'rev-parse', 'HEAD'], text=True).strip()
    if mini_commit != '04d809ceab9df28f9adaed044884180159172930':
        raise ValueError('Upstream mini source changed')
    shutil.copytree(source, candidate, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # The launch operator is frozen too; it is not part of the model harness.
    shutil.copy2(Path(__file__).with_name('run_multistep_study.py'),
                 candidate / 'experiments/infrastructure/run_multistep_study.py')
    task_root.mkdir(parents=True)
    tasks = []
    for name, steps, verifier_timeout, image in TASKS:
        observed = subprocess.check_output(['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock'),
            'image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip()
        if observed != image:
            raise ValueError('Initial task image unavailable')
        upstream = ROOT / 'upstream/scb-harbor-20261006' / name
        target = task_root / name
        shutil.copytree(upstream, target)
        config = target / 'task.toml'
        config.write_text(adapt_config(config.read_text(), image, steps))
        original_hashes, hashes = tree_hashes(upstream), tree_hashes(target)
        changes = [p for p in hashes if hashes[p] != original_hashes.get(p)]
        if changes != ['task.toml'] or set(hashes) != set(original_hashes):
            raise ValueError('Unexpected task changes')
        tasks.append({'task_id': name, 'checkpoints': steps, 'path': str(target),
                      'verifier_timeout_sec': verifier_timeout, 'initial_image_id': image,
                      'original_files_sha256': original_hashes, 'files_sha256': hashes,
                      'adapted_files': changes})
    arms = ['native', 'sg_v2', 'mini_swe']
    order = [{'task_id': TASKS[t][0], 'arm': arms[(t + wave) % 3]}
             for wave in range(3) for t in range(3)]
    registration = {
        'schema': 1, 'experiment': 'scb-transfer01',
        'registered_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'model_calls_before_registration': 0, 'arms': arms, 'tasks': tasks, 'planned_order': order,
        'statistical_units': 3, 'planned_trajectories': 9, 'planned_checkpoint_evaluations': 36,
        'candidate': str(candidate),
        'source_sha256': {p.relative_to(candidate).as_posix(): file_hash(p) for p in sorted(candidate.rglob('*.py'))},
        'upstream_mini_commit': mini_commit, 'harbor_version': '0.24.0',
        'hermes_version': '0.21.3', 'model': 'devin/swe-2',
        'task_source_lock_sha256': file_hash(ROOT / 'setup/scb-public-source01.json'),
        'max_problem_requests': 384, 'max_step_requests': 128, 'max_episodes': 6,
        'step_solver_seconds': 1800, 'upstream_step_solver_seconds': 7200,
        'history_policy': 'new child session per checkpoint with cumulative public requirements',
        'official_grader_feedback_visible': False, 'automatic_retries': 0, 'max_concurrent_problems': 3,
        'integration_gate': 'scb-multistep-smoke02.json',
        'prior_failed_integration_preserved': 'scb-multistep-smoke01.json',
        'checkpoint_lifecycle_gate': 'compose-retention-probe04.json',
        'stop_admission_at': '2026-10-06T14:14:06+00:00',
        'absolute_cloud_stop': '2026-10-06T14:24:06+00:00',
        'scope': 'Fresh problem transfer after development fixes; adapted harness comparison, not official leaderboard. '
                 'Checkpoint outcomes within each problem are dependent. No full-controller restart claim.',
    }
    with regpath.open('x') as handle:
        json.dump(registration, handle, indent=2)
    print(json.dumps({'registration': str(regpath), 'sha256': file_hash(regpath),
                      'model_trials_started': 0, 'integration_gate_required': True}))


if __name__ == '__main__':
    main()
