"""Configured acceptance after the executor exits, without hidden answers."""
import json
import sys
from pathlib import Path

sys.path.insert(0, '/opt/sg')
from supergoal_runtime.v2 import verify

task = json.loads(Path('/task/task.json').read_text())
outcome = verify(Path('/workspace'), task['contract'])
Path('/records/acceptance.json').write_text(json.dumps(outcome, ensure_ascii=False))
