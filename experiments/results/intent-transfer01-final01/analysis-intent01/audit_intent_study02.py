"""Check cohort identity and accounting without rerunning or changing outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tomllib


ROOT = Path('/var/lib/supergoal-lab')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    if output.exists():
        raise FileExistsError(output)
    registration = ROOT / 'setup/registration-intent-transfer01.json'
    reg = json.loads(registration.read_text())
    receipt = json.loads((ROOT / 'setup/intent-transfer01-receipt.json').read_text())
    errors, cases = [], []
    if receipt['status'] != 'complete' or receipt['registration_sha256'] != sha(registration):
        errors.append('Cohort is unfinished or its registration changed')
    for name, expected in reg['source_sha256'].items():
        if sha(Path(reg['candidate']) / name) != expected:
            errors.append('Candidate changed: ' + name)
    for task in reg['tasks']:
        for name, expected in task['files_sha256'].items():
            if sha(Path(task['path']) / name) != expected:
                errors.append('Task source changed: ' + task['task_id'] + '/' + name)
    planned = {(r['index'], r['task_id'], r['arm']) for r in reg['planned_order']}
    observed = [(r['index'], r['task_id'], r['arm']) for r in receipt['rows']]
    if set(observed) != planned or len(observed) != len(planned):
        errors.append('Missing, extra, or duplicate planned rows')
    used_controls = set()
    initial_images = {}
    for row in receipt['rows']:
        case = {'index': row['index'], 'task_id': row['task_id'], 'arm': row['arm']}
        if row.get('result_path'):
            result = json.loads(Path(row['result_path']).read_text())
            if (result.get('verifier_result') or {}).get('rewards') != row.get('raw_rewards'):
                errors.append('Receipt reward changed from result: ' + str(row['index']))
            metadata = (result.get('agent_result') or {}).get('metadata') or {}
            uid = metadata.get('control_id')
            if uid:
                if uid in used_controls:
                    errors.append('Controller reused across rows: ' + uid)
                used_controls.add(uid)
                control = ROOT / 'control' / uid
                report = json.loads((control / 'report.json').read_text())
                journal = json.loads((control / 'request-journal.json').read_text())
                records = journal['records']
                if (report['arm'] != row['arm'] or metadata['study_arm'] != row['arm']
                        or report['max_requests'] != reg['max_requests'] or journal['limit'] != reg['max_requests']):
                    errors.append('Arm or budget identity differs: ' + uid)
                if (len(records) > reg['max_requests'] or report['used_requests'] != len(records)
                        or metadata['physical_requests'] != len(records)
                        or [r['index'] for r in records] != list(range(1, len(records) + 1))):
                    errors.append('Physical request accounting mismatch: ' + uid)
                if any(r['request_model'] != reg['model'] for r in records):
                    errors.append('Unregistered requested model: ' + uid)
                if sum(r['model_calls'] for r in report['rounds']) != len(records):
                    errors.append('Role request accounting mismatch: ' + uid)
                task = next(t for t in reg['tasks'] if t['task_id'] == row['task_id'])
                observed_environment = json.loads((control / 'environment.json').read_text())
                expected_environment = tomllib.loads((Path(task['path']) / 'task.toml').read_text())['environment']
                if (observed_environment['nano_cpus'] != int(expected_environment['cpus'] * 10**9)
                        or observed_environment['memory_limit_bytes'] != expected_environment['memory_mb'] * 1024**2):
                    errors.append('Task resource limits differ: ' + uid)
                initial_images.setdefault(row['task_id'], set()).add(observed_environment['image_id'])
                instruction = (Path(task['path']) / 'instruction.md').read_text()
                if hashlib.sha256(instruction.encode()).hexdigest() != report['original_instruction_sha256']:
                    errors.append('Instruction differs from frozen task: ' + uid)
                case.update(control_id=uid, physical_requests=len(records),
                            response_models=sorted({str(r.get('response_model')) for r in records}),
                            requests_without_complete_usage=sum(any((r.get('usage') or {}).get(k) is None
                                for k in ['input_tokens', 'output_tokens']) for r in records))
        cases.append(case)
    image_checks = []
    for task, ids in initial_images.items():
        images = json.loads(subprocess.check_output(['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock'),
                            'image', 'inspect', *sorted(ids)], text=True))
        fingerprints = set()
        for image in images:
            config = dict(image['Config'])
            labels = dict(config.get('Labels') or {})
            # Harbor gives each isolated trial its own Compose project. This
            # ownership label does not change filesystem layers or app config.
            labels.pop('com.docker.compose.project', None)
            config['Labels'] = labels
            value = {'rootfs': image['RootFS'], 'config': config,
                     'architecture': image['Architecture'], 'os': image['Os']}
            fingerprints.add(hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())
        image_checks.append({'task_id': task, 'image_ids': sorted(ids),
            'filesystem_and_execution_config_fingerprints': sorted(fingerprints),
            'ignored_label': 'com.docker.compose.project'})
        if len(fingerprints) != 1:
            errors.append('Initial filesystem or execution config differs between arms: ' + task)
    audit = {'status': 'passed' if not errors else 'issues_found', 'planned_rows': len(planned),
        'observed_rows': len(observed), 'unique_controllers': len(used_controls), 'cases': cases,
        'registration_sha256': sha(registration), 'source_files_checked': len(reg['source_sha256']),
        'task_files_checked': sum(len(t['files_sha256']) for t in reg['tasks']),
        'operator_sha256': sha(Path(__file__)), 'model_calls': 0, 'raw_rewards_changed': False,
        'initial_images_by_task': {task: sorted(ids) for task, ids in initial_images.items()},
        'image_content_checks': image_checks,
        'prior_audit': 'intent-transfer01-final-audit.json retained; ID-only comparison flagged per-trial Compose labels',
        'errors': errors, 'scope': 'Source, instruction, row and request identity; not semantic correctness or model-weight provenance'}
    output.write_text(json.dumps(audit, indent=2))
    print(json.dumps({k: audit[k] for k in ['status', 'observed_rows', 'unique_controllers', 'errors']}))
    sys.exit(bool(errors))


if __name__ == '__main__':
    main()
