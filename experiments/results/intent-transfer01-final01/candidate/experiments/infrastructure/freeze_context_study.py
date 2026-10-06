"""Freeze the registered 2x2 development design after its integration gate."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'context-handoff01'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base = ROOT / 'candidates/context-dev01'
    candidate = ROOT / 'candidates' / LABEL
    target = ROOT / 'setup' / ('registration-' + LABEL + '.json')
    task_root = ROOT / 'tasks' / LABEL
    if candidate.exists() or target.exists() or task_root.exists():
        raise FileExistsError('Preserve frozen cohorts')
    gate_path = ROOT / 'setup/context-smoke01.json'
    gate = json.loads(gate_path.read_text())
    if gate['status'] != 'passed' or len(gate['rows']) != 4 or any(r['status'] != 'passed' for r in gate['rows']):
        raise ValueError('Actual context/intervention gate did not pass')
    boundary_tests = ROOT / 'grader-history-checks01/junit.xml'
    cases = ET.parse(boundary_tests).findall('.//testcase')
    if len(cases) != 5 or any(c.find('failure') is not None or c.find('error') is not None
                             or c.find('skipped') is not None for c in cases):
        raise ValueError('POSIX grader history boundary tests did not pass')
    for name, expected in gate['candidate_source_sha256'].items():
        if sha(base / name) != expected:
            raise ValueError('Integration candidate changed: ' + name)
    previous = json.loads((ROOT / 'setup/registration-scb-transfer01.json').read_text())
    if [r['task_id'] for r in previous['tasks']] != ['file_backup', 'database_migration', 'dag_execution']:
        raise ValueError('Development task identities changed')
    oracle = json.loads((ROOT / 'setup/scb-oracle01.json').read_text())
    if (oracle['status'] != 'complete' or len(oracle['rows']) != 3
            or {r['task'] for r in oracle['rows']} != {t['task_id'] for t in previous['tasks']}
            or any(r['status'] != 'reference_passed' or r.get('exception')
                   or len(r['steps']) != r['checkpoints']
                   or any(s.get('exception_info') or s['verifier_result']['rewards']['strict_pass_rate'] != 1
                          for s in r['steps']) for r in oracle['rows'])):
        raise ValueError('Reference source check unavailable')
    tasks = []
    for old in previous['tasks']:
        directory = Path(old['path'])
        actual = {p.relative_to(directory).as_posix(): sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}
        if actual != old['files_sha256']:
            raise ValueError('Previously reference-checked task changed')
        image = subprocess.check_output(['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock'),
                                         'image', 'inspect', old['initial_image_id'], '--format', '{{.Id}}'], text=True).strip()
        if image != old['initial_image_id']:
            raise ValueError('Reference-checked initial image changed')
        tasks.append({**old, 'path': str(task_root / old['task_id'])})
    shutil.copytree(base, candidate, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    task_root.mkdir(parents=True)
    for old, new in zip(previous['tasks'], tasks):
        shutil.copytree(old['path'], new['path'])
    for name in ['run_context_study.py', 'freeze_context_study.py']:
        shutil.copy2(Path(__file__).with_name(name), candidate / 'experiments/infrastructure' / name)
    arms = ['carry_ledger', 'carry_current', 'fresh_ledger', 'fresh_current']
    order = [{'task_id': tasks[t]['task_id'], 'arm': arms[(t + wave) % 4]} for wave in range(4) for t in range(3)]
    reg = {'schema': 1, 'experiment': LABEL, 'registered_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'kind': 'outcome-informed development mechanism study; not new holdout',
           'model_calls_before_registration_for_this_formal_cohort': 0, 'arms': arms, 'tasks': tasks,
           'planned_order': order, 'order_policy': 'deterministic rotated order across problems; no outcome-adaptive assignments',
           'statistical_units': 3, 'planned_trajectories': 12, 'planned_checkpoint_evaluations': 48,
           'candidate': str(candidate), 'source_sha256': {p.relative_to(candidate).as_posix(): sha(p) for p in sorted(candidate.rglob('*.py'))},
           'integration_gate_sha256': sha(gate_path), 'reference_receipt_sha256': sha(ROOT / 'setup/scb-oracle01.json'),
           'grader_history_test_junit_sha256': sha(boundary_tests),
           'reference_task_registration_sha256': sha(ROOT / 'setup/registration-scb-transfer01.json'),
           'protocol_sha256': sha(candidate / 'docs/context-handoff-study-2026-10-06.md'),
           'model': 'devin/swe-2', 'hermes_version': '0.21.3', 'harbor_version': '0.24.0',
           'max_problem_requests': 384, 'max_step_requests': 128, 'max_episodes': 6,
           'step_solver_seconds': 1800, 'max_output_tokens_per_request': 6000, 'hard_input_token_limit': None,
           'max_concurrent_problems': 6, 'shared_cpu_pool': 6, 'shared_memory_mb': 49152,
           'minimum_free_disk_gib': 60, 'automatic_retries': 0,
           'stop_admission_at': '2026-10-06T14:14:06+00:00', 'absolute_cloud_stop': '2026-10-06T14:24:06+00:00',
           'primary_endpoint': 'Per-problem mean strict checkpoint score, interpreted with measured intervention activation',
           'contrasts': [['carry_ledger', 'fresh_ledger'], ['carry_current', 'fresh_current'],
                         ['carry_ledger', 'carry_current'], ['fresh_ledger', 'fresh_current']],
           'auditor_contract': 'Identical complete public requirement history in every condition',
           'grader_history': 'Known SCBench quality history concealed during solver/auditor phases, restored before official grading',
           'limitations': ['Three previously observed problem IDs only', 'No autonomous stage discovery claim',
                          'Not a context-window overflow experiment', 'Model-written artifacts and one model judge may share errors',
                          'Actual cost differs despite equal request limits', 'One known grader history file isolated; not proof of no other side channels']}
    archive = ROOT / 'setup' / (LABEL + '-source.tar.gz')
    if archive.exists():
        raise FileExistsError(archive)
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(candidate, arcname=LABEL)
    reg['candidate_archive_sha256'] = sha(archive)
    with target.open('x') as f:
        json.dump(reg, f, indent=2)
    print(json.dumps({'registration': str(target), 'sha256': sha(target), 'source_files': len(reg['source_sha256'])}))


if __name__ == '__main__':
    main()
