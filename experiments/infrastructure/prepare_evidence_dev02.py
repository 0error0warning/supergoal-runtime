"""Second development candidate: schema repair and observation-surface handling."""
import datetime
import hashlib
import json
from pathlib import Path


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/intent-evidence-dev02'


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def main():
    previous = json.loads((ROOT / 'setup/registration-intent-evidence-dev01.json').read_text())
    research = json.loads((ROOT / 'tasks/dr3-intent-development01/provenance.json').read_text())
    tasks = [previous['tasks'][0], research['tasks'][0]]
    for task in tasks:
        task['files_sha256'] = hashes(Path(task['path']))
    rows = [(tasks[0]['task_id'], 'evidence'), (tasks[1]['task_id'], 'native'), (tasks[1]['task_id'], 'evidence')]
    reg = {'experiment': 'intent-evidence-dev02', 'kind': 'development_only',
           'registered_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'candidate': str(CANDIDATE), 'source_sha256': hashes(CANDIDATE), 'tasks': tasks,
           'planned_order': [{'index': i, 'task_id': task, 'arm': arm} for i, (task, arm) in enumerate(rows)],
           'concurrency': 3, 'max_requests': 128, 'max_episodes': 6,
           'absolute_stop': '2026-10-06T14:24:06+00:00', 'model': 'devin/swe-2',
           'hardware': 'n1-highmem-8', 'research_delivery_probe_is_not_quality': True}
    path = ROOT / 'setup/registration-intent-evidence-dev02.json'
    with path.open('x') as handle:
        json.dump(reg, handle, indent=2)
    print(json.dumps({'registration': str(path), 'model_trials_started': 0}))


if __name__ == '__main__':
    main()
