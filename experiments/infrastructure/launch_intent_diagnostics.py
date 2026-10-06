"""Run the pre-registered research diagnostic once, after all solver rows settle."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/intent-transfer01'
REGISTRATION = ROOT / 'setup/registration-intent-transfer01.json'
RECEIPT = ROOT / 'setup/intent-transfer01-receipt.json'


def main():
    target = ROOT / 'setup/intent-transfer01-grader-launch01.json'
    with target.open('x') as stream:
        state = {'status': 'waiting_solvers', 'started_epoch': time.time(),
                 'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        json.dump(state, stream, indent=2)

    def save():
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2))
        temp.replace(target)

    try:
        admission = datetime.datetime.fromisoformat('2026-10-06T14:05:00+00:00').timestamp()
        while json.loads(RECEIPT.read_text())['status'] != 'complete':
            if time.time() > admission:
                raise TimeoutError('Research grading admission closed before the cloud cutoff')
            if subprocess.run(['systemctl', 'is-active', '--quiet', 'supergoal-intent-transfer01.service']).returncode:
                raise RuntimeError('Original solver unit stopped before its final receipt; no automatic restart')
            time.sleep(15)
        if time.time() > admission:
            raise TimeoutError('Research grading admission closed before the cloud cutoff')
        reg = json.loads(REGISTRATION.read_text())
        source = CANDIDATE / 'experiments/public_benchmarks/research_diagnostics.py'
        expected = reg['source_sha256']['experiments/public_benchmarks/research_diagnostics.py']
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen research grading source changed')
        output = ROOT / 'research-grading/intent-transfer01'
        if hashlib.sha256((output / 'rubric-lock.json').read_bytes()).hexdigest() != reg['research_rubric_lock_sha256']:
            raise ValueError('Frozen rubric lock changed')
        state.update(status='grading', grading_source_sha256=expected, grading_started_epoch=time.time())
        save()
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, [CANDIDATE / 'experiments',
                                                   CANDIDATE / 'experiments/public_benchmarks'])))
        with (ROOT / 'setup/intent-transfer01-grading.log').open('x') as log:
            run = subprocess.run([str(ROOT / 'harbor-venv/bin/python'), str(source), 'grade',
                '--registration', str(REGISTRATION), '--receipt', str(RECEIPT), '--output', str(output)],
                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=1020)
        state.update(status='complete' if run.returncode == 0 else 'failed', exit_code=run.returncode)
    except Exception as exc:
        state.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        state['finished_epoch'] = time.time()
        save()


if __name__ == '__main__':
    main()
