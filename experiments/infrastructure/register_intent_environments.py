"""Reference-only preflight; it cannot launch a held-out model condition."""
import datetime
import hashlib
import json
from pathlib import Path


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/intent-evidence-dev02'


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}


tasks = json.loads((ROOT / 'setup/intent-transfer01-environments.json').read_text())['tasks']
research = json.loads((ROOT / 'tasks/dr3-intent-heldout01/provenance.json').read_text())['tasks']
for task in research:
    task['files_sha256'] = hashes(Path(task['path']))
tasks += research
reg = {'experiment': 'intent-environment01', 'kind': 'zero_model_reference_preflight',
       'registered_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'candidate': str(CANDIDATE), 'source_sha256': hashes(CANDIDATE), 'tasks': tasks,
       'planned_order': [], 'concurrency': 3, 'max_requests': 0, 'max_episodes': 1,
       'absolute_stop': '2026-10-06T14:24:06+00:00', 'model_calls': 0}
path = ROOT / 'setup/registration-intent-environment01.json'
with path.open('x') as handle:
    json.dump(reg, handle, indent=2)
print(json.dumps({'registration': str(path), 'tasks': len(tasks), 'model_trials_started': 0}))
