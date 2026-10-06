"""Wait only for the already-started reference gate, then run the frozen study."""
import hashlib
import json
import os
from pathlib import Path
import time


ROOT = Path('/var/lib/supergoal-lab')
candidate = ROOT / 'candidates/intent-transfer01'
registration = ROOT / 'setup/registration-intent-transfer01.json'
receipt = ROOT / 'setup/intent-transfer01-launch.json'
if receipt.exists():
    raise FileExistsError('Never relaunch an admitted study automatically')
receipt.write_text(json.dumps({'status': 'waiting_reference', 'created_epoch': time.time(),
    'registration_sha256': hashlib.sha256(registration.read_bytes()).hexdigest(),
    'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, indent=2))
reference = ROOT / 'setup/intent-environment01-oracles-receipt.json'
while json.loads(reference.read_text())['status'] != 'complete':
    time.sleep(15)
python = str(ROOT / 'harbor-venv/bin/python')
os.execv(python, [python, str(candidate / 'experiments/infrastructure/evidence_operator.py'), str(registration)])
