"""Freeze six paired continuations only after identity and reference gates pass."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from prefix_inputs import load_prefix


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'context-prefix01'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(directory):
    return {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob('*.py')}


def main():
    base, candidate = ROOT / 'candidates/prefix-dev02', ROOT / 'candidates' / LABEL
    target = ROOT / 'setup' / ('registration-' + LABEL + '.json')
    archive = ROOT / 'setup' / (LABEL + '-source.tar.gz')
    if any(p.exists() for p in (candidate, target, archive)):
        raise FileExistsError('Preserve frozen cohorts')
    paths = {'inputs': ROOT / 'setup/context-prefix01-inputs.json',
             'integration': ROOT / 'setup/prefix-smoke04.json',
             'reference': ROOT / 'setup/prefix-oracle02.json',
             'lifecycle': ROOT / 'setup/compose-retention-probe04.json'}
    gates = {k: json.loads(p.read_text()) for k, p in paths.items()}
    if gates['inputs']['status'] != 'prepared' or gates['lifecycle']['status'] != 'passed':
        raise ValueError('Inputs or checkpoint lifecycle gate not ready')
    for name, count, success in [('integration', 2, 'passed'), ('reference', 3, 'reference_passed')]:
        gate = gates[name]
        if (gate['status'] != 'passed' or len(gate['rows']) != count
                or any(r['status'] != success for r in gate['rows'])
                or sources(base) != gate['candidate_source_sha256']):
            raise ValueError('Candidate differs from passed gate: ' + name)
    if {r['arm'] for r in gates['integration']['rows']} != {'fresh_ledger', 'fresh_current'}:
        raise ValueError('Integration conditions differ')
    tasks = gates['inputs']['tasks']
    if [t['task_id'] for t in tasks] != ['file_backup', 'database_migration', 'dag_execution']:
        raise ValueError('Problem identity or order changed')
    refs = {r['task_id']: r for r in gates['reference']['rows']}
    if set(refs) != {t['task_id'] for t in tasks}:
        raise ValueError('Reference task identities differ')
    for task in tasks:
        directory = Path(task['path'])
        actual = {p.relative_to(directory).as_posix(): sha(p) for p in directory.rglob('*') if p.is_file()}
        if actual != task['files_sha256']:
            raise ValueError('Prepared task changed: ' + task['task_id'])
        spec, _ = load_prefix(task['prefix_spec'], task['prefix_spec_sha256'])
        if (spec['image_id'] != task['initial_image_id'] or spec['workspace_sha256'] != task['workspace_sha256']
                or sha(Path(task['prefix_test_log'])) != task['prefix_test_log_sha256']):
            raise ValueError('Prepared prefix changed')
        image = subprocess.check_output(['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock'),
                'image', 'inspect', spec['image_id'], '--format', '{{.Id}}'], text=True).strip()
        if image != spec['image_id']:
            raise ValueError('Common image unavailable')
        row = refs[task['task_id']]
        if (row.get('exception') or len(row['steps']) != task['checkpoints']
                or sha(Path(row['result_path'])) != row['result_sha256']
                or [s['step_name'] for s in row['steps']] != task['checkpoint_names']
                or any(s.get('exception_info') or
                       s['verifier_result']['rewards']['strict_pass_rate'] != 1 for s in row['steps'])):
            raise ValueError('Reference suffix not fully verified')
    if gates['reference']['inputs_sha256'] != sha(paths['inputs']):
        raise ValueError('Reference input receipt changed')
    shutil.copytree(base, candidate, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ['run_prefix_study.py', 'freeze_prefix_study.py']:
        shutil.copy2(Path(__file__).with_name(name), candidate / 'experiments/infrastructure' / name)
    arms = ['fresh_ledger', 'fresh_current']
    order = [{'task_id': tasks[t]['task_id'], 'arm': arms[(t + wave) % 2]}
             for wave in range(2) for t in range(3)]
    reg = {'schema': 1, 'experiment': LABEL, 'registered_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'kind': 'outcome-informed development; paired suffixes from one prefix per problem, not holdout',
           'model_calls_before_registration_for_this_formal_cohort': 0,
           'arms': arms, 'tasks': tasks, 'planned_order': order,
           'order_policy': 'deterministic alternating arm by task then inverted second wave; no adaptive assignments',
           'statistical_units': 3, 'planned_trajectories': 6, 'planned_checkpoint_evaluations': 18,
           'candidate': str(candidate), 'source_sha256': sources(candidate),
           'pinned_files': {k: {'path': str(p), 'sha256': sha(p)} for k, p in paths.items()},
           'prefix_checkpoint_count': 1, 'prefix_requests_observed_once': sum(t['prefix_requests'] for t in tasks),
           'prefix_requests_charged_to_suffix': 0,
           'protocol_sha256': sha(candidate / 'docs/context-prefix-study-2026-10-06.md'),
           'model': 'devin/swe-2', 'hermes_version': '0.21.3', 'harbor_version': '0.24.0',
           'max_problem_requests': 384, 'max_step_requests': 128, 'max_episodes': 6,
           'step_solver_seconds': 1800, 'max_output_tokens_per_request': 6000, 'hard_input_token_limit': None,
           'max_concurrent_problems': 6, 'shared_cpu_pool': 6, 'shared_memory_mb': 49152,
           'minimum_free_disk_gib': 60, 'automatic_retries': 0,
           'stop_admission_at': '2026-10-06T14:14:06+00:00', 'absolute_cloud_stop': '2026-10-06T14:24:06+00:00',
           'primary_endpoint': 'Per-problem paired difference in suffix mean strict score: replay minus current-only',
           'contrasts': [['fresh_current', 'fresh_ledger']],
           'auditor_contract': 'Identical full cumulative public requirements in both conditions',
           'grader_history': 'Common protected prefix baseline restored, concealed before each model phase, restored before grading',
           'limitations': ['Three previously observed IDs, one prefix each', 'Remaining model randomness is not controlled',
                          'Same files and fresh dialogue in both arms; auditor feedback can reveal old requirements',
                          'No cross-day autonomy or overall superiority claim', 'Known grader-history isolation only']}
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(candidate, arcname=LABEL)
    reg['candidate_archive_sha256'] = sha(archive)
    with target.open('x') as stream:
        json.dump(reg, stream, indent=2)
    print(json.dumps({'registration': str(target), 'sha256': sha(target), 'source_files': len(reg['source_sha256'])}))


if __name__ == '__main__':
    main()
