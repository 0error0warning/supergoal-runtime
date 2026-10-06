"""No-model validation of the exact namespace used by reserved experiments."""
import json
import subprocess

from supervisor import ROOT, BUNDLES, dump, sandbox, user_dir


def main():
    run = ROOT / 'runs' / ('preflight-' + BUNDLES.name)
    run.mkdir()
    for name in ('home', 'workspace', 'records', 'workspace/inputs'):
        user_dir(run / name)
    (run / 'task').mkdir()
    (run / 'workspace/inputs/batch-0.json').write_text('[]')
    dump(run / 'task/task.json', {'id': 'preflight'})
    code = """import json,os
from pathlib import Path
hidden=['/opt/study/tasks.py','/opt/study/supervisor.py','/opt/study/soak.py','/task/cases.json','/records/kernel.sqlite','/var/lib/supergoal-lab/private/model.json']
assert all(not Path(p).exists() for p in hidden), 'hidden study state exposed'
assert os.geteuid()==993
try:
    Path('/workspace/inputs/CLOSED').write_text('forbidden')
except OSError:
    pass
else:
    raise AssertionError('executor can mutate inputs')
status=Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in status
print(json.dumps({'uid':os.geteuid(),'hidden_paths_absent':True,'inputs_read_only':True,'capabilities_empty':True,'runtime_files':sorted(p.name for p in Path('/opt/study').iterdir())}))
"""
    command = sandbox(run, 'sg_v2')
    path = run / 'launch.json'
    args = json.loads(path.read_text())
    args[-1:] = ['-c', code]
    dump(path, args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    receipt = json.loads(result.stdout)
    dump(ROOT / 'receipts' / ('preflight-' + BUNDLES.name + '.json'), receipt)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
