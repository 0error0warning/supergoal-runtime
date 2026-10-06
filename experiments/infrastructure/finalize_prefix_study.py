"""Finite evidence export after the one registered prefix controller stops."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'context-prefix01'
OPERATOR = ROOT / 'analysis-prefix01'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory():
    return {p.relative_to(OPERATOR).as_posix(): sha(p) for p in OPERATOR.rglob('*.py')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller-invocation', required=True)
    args = parser.parse_args()
    receipt_path = ROOT / 'setup' / (LABEL + '-finalizer01.json')
    if receipt_path.exists():
        raise FileExistsError('Preserve prior observer')
    sources = inventory()
    state = {'status': 'waiting_registered_process', 'started_epoch': time.time(),
             'operator_sha256': sources, 'new_model_calls': 0, 'automatic_trial_retries': 0}

    def save():
        temp = receipt_path.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2))
        temp.replace(receipt_path)

    save()
    try:
        deadline = datetime.datetime.fromisoformat('2026-10-06T14:21:06+00:00').timestamp()
        while True:
            receipt = json.loads((ROOT / 'setup' / (LABEL + '-receipt.json')).read_text())
            unit = dict(line.split('=', 1) for line in subprocess.check_output([
                'systemctl', 'show', 'supergoal-' + LABEL + '.service',
                '-p', 'MainPID', '-p', 'ActiveState', '-p', 'InvocationID'], text=True).splitlines())
            pid = int(unit['MainPID'])
            alive = pid > 0 and Path(f'/proc/{pid}').exists()
            state.update(checked_epoch=time.time(), controller_unit=unit, controller_process_alive=alive)
            save()
            if receipt['status'] == 'complete_with_itemized_outcomes' and not alive:
                break
            if not alive or unit['InvocationID'] != args.controller_invocation:
                raise RuntimeError('Original controller stopped or changed before terminal receipt; no restart')
            if time.time() >= deadline:
                raise TimeoutError('Evidence-export admission expired')
            time.sleep(15)
        if inventory() != sources:
            raise RuntimeError('Frozen observer source changed')
        state.update(status='exporting', operations=[], outputs={})
        save()
        setup = ROOT / 'setup'
        registration = setup / ('registration-' + LABEL + '.json')
        public = OPERATOR / 'experiments/public_benchmarks'
        observation = setup / (LABEL + '-final-observation01.json')
        regressions = setup / (LABEL + '-final-regressions01.json')
        analysis = setup / (LABEL + '-final-analysis01.json')
        progress = setup / (LABEL + '-final-progress01.md')
        audit = setup / (LABEL + '-final-audit01.json')
        operations = [
            ('observation', public / 'collect_multistep.py', ['--registration', str(registration), '--output', str(observation)], [observation]),
            ('audit', OPERATOR / 'audit_completed_prefix.py', ['--output', str(audit)], [audit]),
            ('regressions', public / 'audit_multistep_regressions.py', ['--registration', str(registration), '--output', str(regressions)], [regressions]),
            ('analysis', public / 'analyze_prefix.py', ['--bundle', str(observation), '--receipt', str(setup / (LABEL + '-receipt.json')),
                '--regressions', str(regressions), '--registration', str(registration), '--output', str(analysis),
                '--markdown-output', str(progress)], [analysis, progress]),
        ]
        for name, script, argv, paths in operations:
            if any(p.exists() for p in paths):
                raise FileExistsError('Preserve previous final export')
            proc = subprocess.run([sys.executable, str(script), *argv], capture_output=True, text=True, timeout=180)
            state['operations'].append({'name': name, 'returncode': proc.returncode,
                                        'error': proc.stderr[-3000:] if proc.returncode else None})
            for path in paths:
                if path.exists():
                    state['outputs'][path.name] = {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}
            save()
        if analysis.exists():
            state['context_audit_errors'] = json.loads(analysis.read_text())['audit_errors']
        state.update(status='complete' if all(op['returncode'] == 0 for op in state['operations'])
                     and not state.get('context_audit_errors') else 'complete_with_audit_issues', finished_epoch=time.time())
        save()
    except BaseException as exc:
        state.update(status='export_failed', error=type(exc).__name__ + ': ' + str(exc), finished_epoch=time.time())
        save()
        raise


if __name__ == '__main__':
    main()
