"""Freeze a known development problem; no transfer claim for this task."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/intent-evidence-dev01'


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def main():
    task = ROOT / 'upstream/terminal-bench-2-1/tasks/configure-git-webserver'
    meta = tomllib.loads((task / 'task.toml').read_text())
    image_id = subprocess.check_output(['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock'),
        'image', 'inspect', meta['environment']['docker_image'], '--format', '{{.Id}}'], text=True).strip()
    reg = {'experiment': 'intent-evidence-dev01', 'kind': 'known_development_only',
           'registered_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'candidate': str(CANDIDATE), 'source_sha256': hashes(CANDIDATE),
           'tasks': [{'task_id': task.name, 'path': str(task), 'files_sha256': hashes(task),
                      'image_id': image_id, 'solver_seconds': 900, 'verifier_seconds': 900}],
           'planned_order': [{'index': i, 'task_id': task.name, 'arm': arm}
                             for i, arm in enumerate(['native', 'sg_v2', 'evidence'])],
           'concurrency': 3, 'max_requests': 128, 'max_episodes': 6,
           'absolute_stop': '2026-10-06T14:24:06+00:00', 'model': 'devin/swe-2',
           'hardware': 'n1-highmem-8', 'external_hidden_scores_visible': False}
    path = ROOT / 'setup/registration-intent-evidence-dev01.json'
    with path.open('x') as handle:
        json.dump(reg, handle, indent=2)
    print(json.dumps({'registration': str(path), 'model_trials_started': 0}))


if __name__ == '__main__':
    main()
