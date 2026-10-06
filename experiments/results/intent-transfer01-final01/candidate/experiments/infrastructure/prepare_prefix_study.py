"""Prepare outcome-independent common origins; do not run models or references."""
import copy
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

from prefix_inputs import WORKSPACE_PROBE, first_registered_origins


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'context-prefix01'
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def suffix_toml(text, image):
    original = tomllib.loads(text)
    blocks = text.split('[[steps]]')
    if (len(blocks) != len(original['steps']) + 1 or len(original['steps']) < 2
            or original['steps'][0]['name'] != 'checkpoint_1'):
        raise ValueError('Unexpected source step structure')
    header, replaced = re.subn(r'^docker_image = "[^"]+"$', 'docker_image = "' + image + '"', blocks[0], flags=re.MULTILINE)
    if replaced != 1:
        raise ValueError('Expected exactly one prebuilt image declaration')
    changed = header + ''.join('[[steps]]' + block for block in blocks[2:])
    expected = copy.deepcopy(original)
    expected['environment']['docker_image'] = image
    expected['steps'] = original['steps'][1:]
    if tomllib.loads(changed) != expected:
        raise ValueError('Suffix preparation changed more than image and first step')
    return changed


def main():
    source_registration = ROOT / 'setup/registration-context-handoff01.json'
    source_receipt = ROOT / 'setup/context-handoff01-receipt.json'
    final_audit = ROOT / 'setup/context-handoff01-final-audit01.json'
    reg = json.loads(source_registration.read_text())
    receipt = json.loads(source_receipt.read_text())
    audit = json.loads(final_audit.read_text())
    if (receipt['status'] != 'complete_with_itemized_outcomes' or audit['status'] != 'passed'
            or sha(source_registration) != receipt['registration_sha256']
            or sha(source_receipt) != audit['receipt_sha256']):
        raise ValueError('Common-origin cohort is not finally audited')
    inputs = ROOT / 'prefix-inputs01'
    tasks = ROOT / 'tasks' / LABEL
    target = ROOT / 'setup' / (LABEL + '-inputs.json')
    if any(p.exists() for p in (inputs, tasks, target)):
        raise FileExistsError('Preserve existing input preparation')
    origins = first_registered_origins(reg, receipt)
    selected = []
    for task in reg['tasks']:
        old = Path(task['path'])
        files = {p.relative_to(old).as_posix(): sha(p) for p in old.rglob('*') if p.is_file()}
        if files != task['files_sha256']:
            raise ValueError('Audited source task changed')
        row = origins[task['task_id']]
        result_path = Path(row['result_path'])
        result = json.loads(result_path.read_text())
        metadata = result['step_results'][0]['agent_result']['metadata']
        control = ROOT / 'control' / metadata['control_id']
        report = json.loads((control / 'report.json').read_text())
        problem = ROOT / 'control' / ('scb-' + metadata['problem_id'])
        ledger = json.loads((problem / 'problem.json').read_text())
        history = problem / 'grader-history-2.json'
        if sha(history) != ledger['grader_history_boundaries'][1]['backup_sha256']:
            raise ValueError('Original first-grade history changed')
        image = report['artifact_image_id']
        actual = subprocess.check_output(DOCKER + ['image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip()
        if actual != image:
            raise ValueError('Selected image unavailable; restore this exact archive, never substitute an origin')
        measured = json.loads(subprocess.check_output(DOCKER + [
            'run', '--rm', '--name', 'sg-prefix-probe-' + task['task_id'], '--network=none',
            '--cpus=1', '--memory=1g', '--pids-limit=128', '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m',
            '--user', '0:0', '--entrypoint', 'python3', image, '-c', WORKSPACE_PROBE], text=True, timeout=90))
        changed_toml = suffix_toml((old / 'task.toml').read_text(), image)
        selected.append((task, row, result_path, metadata, control, history, image, measured, changed_toml))
    inputs.mkdir(mode=0o700)
    tasks.mkdir()
    prepared = []
    for task, row, result_path, metadata, control, history, image, measured, changed_toml in selected:
        old = Path(task['path'])
        directory = inputs / task['task_id']
        directory.mkdir(mode=0o700)
        public = directory / 'prefix-instruction.md'
        backup = directory / 'grader-history.json'
        grading_log = directory / 'prefix-test-stdout.txt'
        shutil.copyfile(old / 'steps/checkpoint_1/instruction.md', public)
        shutil.copyfile(history, backup)
        shutil.copyfile(result_path.parent / 'steps/checkpoint_1/verifier/test-stdout.txt', grading_log)
        backup.chmod(0o600)
        spec = {'schema': 1, 'task_id': task['task_id'], 'prefix_checkpoint_count': 1,
                'image_id': image, **measured,
                'public_requirement': {'path': str(public), 'sha256': sha(public)},
                'grader_history': {'path': str(backup), 'sha256': sha(backup)},
                'prefix_requests': json.loads((control / 'report.json').read_text())['used_requests'],
                'origin': {'experiment': reg['experiment'], 'registered_index': row['index'], 'arm': row['arm'],
                           'problem_id': metadata['problem_id'], 'control_id': metadata['control_id'],
                           'result_sha256': sha(result_path), 'report_sha256': sha(control / 'report.json')}}
        spec_path = directory / 'prefix.json'
        spec_path.write_text(json.dumps(spec, indent=2))
        destination = tasks / task['task_id']
        shutil.copytree(old, destination, ignore=lambda path, names: ['checkpoint_1'] if Path(path) == old / 'steps' else [])
        (destination / 'task.toml').write_text(changed_toml)
        parsed = tomllib.loads(changed_toml)
        prepared.append({'task_id': task['task_id'], 'path': str(destination), 'checkpoints': task['checkpoints'] - 1,
                         'checkpoint_names': [s['name'] for s in parsed['steps']], 'prefix_checkpoint_count': 1,
                         'initial_image_id': image, 'verifier_timeout_sec': task['verifier_timeout_sec'],
                         'prefix_spec': str(spec_path), 'prefix_spec_sha256': sha(spec_path),
                         'prefix_test_log': str(grading_log), 'prefix_test_log_sha256': sha(grading_log),
                         'prefix_requests': spec['prefix_requests'], 'workspace_sha256': spec['workspace_sha256'],
                         'files_sha256': {p.relative_to(destination).as_posix(): sha(p) for p in destination.rglob('*') if p.is_file()}})
    result = {'status': 'prepared', 'experiment': LABEL,
              'prepared_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'origin_selection': 'First registered occurrence of each problem, regardless of observed score',
              'source_registration_sha256': sha(source_registration), 'source_receipt_sha256': sha(source_receipt),
              'source_final_audit_sha256': sha(final_audit), 'operator_sha256': sha(Path(__file__)),
              'new_model_calls': 0, 'tasks': prepared}
    with target.open('x') as file:
        json.dump(result, file, indent=2)
    print(json.dumps({'status': result['status'], 'tasks': len(prepared), 'remaining_checkpoints_per_arm': sum(t['checkpoints'] for t in prepared),
                      'output': str(target), 'new_model_calls': 0}))


if __name__ == '__main__':
    main()
