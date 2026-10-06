"""Post-run corroboration of the staged soak's final reproducible deliverable.

This adds an explicitly separate check, without rewriting its registered score.
Run only once the primary soak receipt exists. Execute the submitted script in
a fresh, network-disabled sandbox with no preexisting output; compare outside.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

STUDY = Path(os.environ['SUPERGOAL_STUDY_BUNDLES'])/'study'
sys.path.insert(0, str(STUDY))
from supervisor import ROOT, artifact_bytes, dump, equivalent, sandbox, user_dir


def main(run_id):
    if run_id not in {'soak-v2-r3-20261005', 'soak-v2-20261005'}:
        raise ValueError('Only the two recorded laboratory soaks are supported')
    run = ROOT/'runs'/run_id
    receipt = ROOT/'receipts'/(run_id+'-results.json')
    report = json.loads(receipt.read_text())
    target = ROOT/'receipts'/(run_id+'-postrun-audit.json')
    if target.exists():
        raise RuntimeError('Do not replace a previous audit attempt')
    edition = 99
    directory = run/f'grading-{edition}'
    if directory.exists():
        raise RuntimeError('Audit workspace already exists')
    for name in ('records', 'workspace', 'workspace/inputs'):
        user_dir(directory/name)
    (directory/'task').mkdir()
    (directory/'workspace/update.py').write_bytes(artifact_bytes(run/'workspace', 'update.py'))
    input_metadata = []
    latest = {}
    for source in sorted((run/'workspace/inputs').iterdir()):
        if source.name == 'CLOSED' or (source.name.startswith('batch-') and source.suffix == '.json'):
            content = artifact_bytes(run/'workspace', 'inputs/'+source.name)
            (directory/'workspace/inputs'/source.name).write_bytes(content)
            input_metadata.append({'name': source.name, 'mtime': source.stat().st_mtime,
                                   'sha256': hashlib.sha256(content).hexdigest()})
            if source.suffix == '.json':
                for item in json.loads(content):
                    latest[item['id']] = item['value']
    expected = {'count': len(latest), 'sum': sum(latest.values()), 'ids': sorted(latest)}
    command = sandbox(run, 'sg_v2', grade=True, episode=edition)
    launch = Path(command[-1])
    argv = json.loads(launch.read_text())
    assert argv[-1] == '/opt/study/grade_code.py'
    argv[-1:] = ['-c', "import json,subprocess,sys; from pathlib import Path; p=subprocess.run([sys.executable,'update.py'],capture_output=True,timeout=20); value=json.loads(Path('summary.json').read_text()); Path('/records/reproduction.json').write_text(json.dumps(value)); raise SystemExit(p.returncode)"]
    dump(launch, argv)
    start = time.time()
    try:
        process = subprocess.run(command, capture_output=True, timeout=30, check=False)
        actual_path = directory/'records/reproduction.json'
        actual = json.loads(actual_path.read_text()) if actual_path.exists() else None
        reproduction_pass = process.returncode == 0 and equivalent(actual, expected)
        execution = {'returncode': process.returncode}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        reproduction_pass = False
        execution = {'error_type': type(exc).__name__}
    kernel_path = run/'control/kernel.sqlite'
    kernel_state = None
    if kernel_path.exists():
        db = sqlite3.connect(f'file:{kernel_path}?mode=ro', uri=True)
        try:
            kernel_state = {'goal': db.execute('SELECT status,turns FROM goals WHERE id=?', (run_id,)).fetchone(),
                            'pending_work': db.execute("SELECT count(*) FROM work WHERE status IN ('ready','running')").fetchone()[0],
                            'receipts': db.execute('SELECT count(*) FROM receipts').fetchone()[0]}
        finally:
            db.close()
    waiting = []
    for index, episode in enumerate(report['episodes'][:-1]):
        next_input = next(x for x in input_metadata if x['name'] == f'batch-{index+1}.json')
        start_wait, end_wait = episode['boundary_time'], next_input['mtime']
        waiting.append({'after_phase': index, 'from': start_wait, 'to': end_wait,
                        'seconds': max(0, end_wait-start_wait),
                        'model_requests': sum(start_wait < r['started'] < end_wait for r in report['requests'])})
    audit = {'kind': 'supplementary_postrun_soak_audit', 'run_id': run_id,
             'registered_score_unchanged': True, 'original_registered_pass': report['passed'],
             'fresh_script_reproduction_pass': reproduction_pass, 'execution': execution,
             'execution_seconds': time.time()-start, 'kernel': kernel_state,
             'observed_wait_intervals': waiting, 'inputs': input_metadata,
             'script_sha256': hashlib.sha256(artifact_bytes(run/'workspace', 'update.py')).hexdigest(),
             'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'interpretation': 'Final-script reproduction and current terminal state corroboration. Does not revise the registered score or prove arbitrary crash recovery.'}
    dump(target, audit)
    print(json.dumps(audit))


if __name__ == '__main__':
    main(sys.argv[1])
