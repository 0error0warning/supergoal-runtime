"""Finite, read-only export after the existing registered controller exits.

Never launch, retry, regrade, or modify a model trial. This process can finish
the evidence capture after the development workstation disconnects.
"""
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path('/var/lib/supergoal-lab')
OPERATOR = ROOT / 'analysis-context01'
LABEL = 'context-handoff01'
INVOCATION = '1b2d67f71002474a93e288df8417b333'
DEADLINE = datetime.datetime.fromisoformat('2026-10-06T14:21:06+00:00').timestamp()
RECEIPT = ROOT / 'setup' / (LABEL + '-finalizer01.json')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(value):
    temp = RECEIPT.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(RECEIPT)


def main():
    if RECEIPT.exists():
        raise FileExistsError('Do not restart an existing finalizer')
    source = {p.name: sha(p) for p in OPERATOR.glob('*.py')}
    state = {'status': 'waiting_registered_process', 'started_epoch': time.time(),
             'operator_sha256': source, 'new_model_calls': 0, 'automatic_trial_retries': 0}
    save(state)
    try:
        while True:
            batch = json.loads((ROOT / 'setup' / (LABEL + '-receipt.json')).read_text())
            unit = dict(line.split('=', 1) for line in subprocess.check_output([
                'systemctl', 'show', 'supergoal-context-handoff01.service',
                '-p', 'MainPID', '-p', 'ActiveState', '-p', 'InvocationID'], text=True).splitlines())
            pid = int(unit['MainPID'])
            alive = pid > 0 and Path(f'/proc/{pid}').exists()
            state.update(checked_epoch=time.time(), controller_unit=unit, controller_process_alive=alive)
            save(state)
            if batch['status'] == 'complete_with_itemized_outcomes' and not alive:
                break
            if not alive or unit['InvocationID'] != INVOCATION:
                raise RuntimeError('Registered process stopped or changed before terminal receipt; never restart it')
            if time.time() >= DEADLINE:
                raise TimeoutError('Existing experiment exceeded evidence-export admission deadline')
            time.sleep(15)
        if source != {p.name: sha(p) for p in OPERATOR.glob('*.py')}:
            raise RuntimeError('Read-only observer source changed while waiting')
        state['status'] = 'exporting'
        state['operations'] = []
        save(state)
        registration = ROOT / 'setup' / ('registration-' + LABEL + '.json')
        outputs = {}
        for name, script, extra in [
            ('observation', 'collect_multistep.py', ['--registration', str(registration)]),
            ('audit', 'audit_completed_context.py', []),
            ('regressions', 'audit_multistep_regressions.py', ['--registration', str(registration)]),
        ]:
            path = ROOT / 'setup' / f'{LABEL}-final-{name}01.json'
            if path.exists():
                raise FileExistsError(path)
            proc = subprocess.run([sys.executable, str(OPERATOR / script), *extra, '--output', str(path)],
                                  capture_output=True, text=True, timeout=180)
            state['operations'].append({'name': name, 'returncode': proc.returncode,
                                        'error': proc.stderr[-2000:] if proc.returncode else None})
            if path.exists():
                outputs[name] = {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}
            save(state)
        if 'observation' in outputs:
            candidate = json.loads(registration.read_text())['candidate']
            sys.path.insert(0, candidate)
            spec = importlib.util.spec_from_file_location('context_analysis', OPERATOR / 'analyze_context.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            bundle_path = Path(outputs['observation']['path'])
            result = module.analyze(json.loads(bundle_path.read_text()), json.loads(registration.read_text()))
            result['source_sha256'] = {str(p): sha(p) for p in [bundle_path, registration, OPERATOR / 'analyze_context.py']}
            for name, data, suffix in [('analysis', json.dumps(result, indent=2), 'json'),
                                        ('progress', module.markdown(result), 'md')]:
                path = ROOT / 'setup' / f'{LABEL}-final-{name}01.{suffix}'
                with path.open('x') as stream:
                    stream.write(data)
                outputs[name] = {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size}
            state['context_audit_errors'] = result['audit_errors']
        state.update(status='complete' if not any(op['returncode'] for op in state['operations'])
                     and not state.get('context_audit_errors') else 'complete_with_audit_issues',
                     finished_epoch=time.time(), outputs=outputs)
        save(state)
    except BaseException as exc:
        state.update(status='export_failed', error=type(exc).__name__ + ': ' + str(exc), finished_epoch=time.time())
        save(state)
        raise


if __name__ == '__main__':
    main()
