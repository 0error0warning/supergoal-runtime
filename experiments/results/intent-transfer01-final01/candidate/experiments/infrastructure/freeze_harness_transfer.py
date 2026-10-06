"""Freeze a finite, prospectively selected four-harness transfer cohort."""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'harness-transfer01'
BASE = ROOT / 'candidates/longhorizon-dev01'
CANDIDATE = ROOT / 'candidates' / LABEL
STAGED = Path(__file__).parent / 'overlay'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def save(path, data):
    with path.open('x') as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def main():
    selection_path = ROOT / 'setup' / ('selection-' + LABEL + '.json')
    preparation_path = ROOT / 'setup' / (LABEL + '-preparation.json')
    selection, preparation = read(selection_path), read(preparation_path)
    if preparation['status'] != 'complete_with_itemized_outcomes':
        raise RuntimeError('Reference checks are not finished')
    if [t['task_id'] for t in selection['tasks']] != [
            'adaptive-rejection-sampler', 'polyglot-rust-c', 'gcode-to-text']:
        raise ValueError('Prospective task IDs changed')
    if CANDIDATE.exists() or (ROOT / 'setup' / ('registration-' + LABEL + '.json')).exists():
        raise FileExistsError('A frozen registration already exists')
    if list((ROOT / 'jobs').glob('tb-' + LABEL + '-*')):
        raise RuntimeError('A model trial already exists')
    smoke = read(ROOT / 'setup/registration-longhorizon-smoke01.json')
    for name, expected in smoke['candidate_source_sha256'].items():
        if sha(BASE / name) != expected:
            raise ValueError('Smoke candidate changed: ' + name)
    gates = ['mini-transport-smoke02.json', 'mechanism-pause-smoke01.json',
             'longhorizon-smoke01.json', 'scb-multistep-smoke02.json']
    for gate in gates:
        if read(ROOT / 'setup' / gate)['status'] != 'passed':
            raise ValueError('Integration gate failed: ' + gate)
    upstreams = {'mini-swe-agent': '04d809ceab9df28f9adaed044884180159172930',
                 'longhorizon-harness': 'a1dd930614972b92361c1b9cd6aac441a6db5a65',
                 'terminal-bench-2-1': '7131e4375048a0e408a8fb404b5f499d726b695b'}
    for name, pin in upstreams.items():
        repo = ROOT / 'upstream' / name
        actual = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True)
        if actual != pin or dirty:
            raise ValueError('Pinned upstream changed: ' + name)
    shutil.copytree(BASE, CANDIDATE, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for path in sorted(STAGED.rglob('*')):
        if path.is_file():
            target = CANDIDATE / path.relative_to(STAGED)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    rows = {row['task_id']: row for row in preparation['rows']}
    unavailable = [t['task_id'] for t in selection['tasks'] if not rows[t['task_id']].get('reference_passed')]
    environment = {'experiment': LABEL, 'tasks': [
        {'task_id': t['task_id'], 'files_sha256': t['files_sha256'],
         'image_tag': t['docker_image_tag'], 'image_id': rows[t['task_id']]['image_id']}
        for t in selection['tasks'] if t['task_id'] not in unavailable]}
    environment_path = CANDIDATE / 'experiments/public_benchmarks' / ('environment-' + LABEL + '.json')
    save(environment_path, environment)
    sources = {p.relative_to(CANDIDATE).as_posix(): sha(p)
               for p in sorted(CANDIDATE.rglob('*')) if p.is_file() and p.suffix in {'.py', '.md'}}
    arms = ['native', 'sg_v2', 'mini_swe', 'longhorizon']
    reg = {
        'schema': 1, 'experiment': LABEL, 'kind': 'frozen_new_task_transfer_descriptive',
        'registered_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'model_calls_before_registration': 0,
        'tasks': selection['tasks'], 'arms': arms,
        'planned_order': [{'task_id': task['task_id'], 'arm': arms[(cycle + i) % len(arms)]}
                          for cycle in range(len(arms)) for i, task in enumerate(selection['tasks'])],
        'max_upstream_requests_per_trial': 192, 'max_output_tokens_per_request': 6000,
        'max_outer_episodes': 6, 'repeats_per_task_arm': 1,
        'executor_description': 'Hermes 0.21.3; actual upstream mini-SWE-agent 2.4.6; actual upstream LongHorizon 0.1.7 with Hermes role backend; all devin/swe-2',
        'agent_import_paths': {'native': 'checkpoint_agent:CheckpointStudy', 'sg_v2': 'checkpoint_agent:CheckpointStudy',
                              'mini_swe': 'checkpoint_agent:CheckpointMiniStudy', 'longhorizon': 'longhorizon_agent:LongHorizonStudy'},
        'source_sha256': sources, 'upstream_commits': upstreams,
        'official_source_commit': upstreams['terminal-bench-2-1'],
        'mini_upstream_commit': upstreams['mini-swe-agent'],
        'longhorizon_upstream_commit': upstreams['longhorizon-harness'],
        'environment_lock_file': environment_path.name, 'environment_lock_sha256': sha(environment_path),
        'environment_unavailable_tasks': unavailable, 'oracles': preparation['rows'],
        'start_after_receipt': preparation_path.name,
        'mini_integration_gate': gates[0], 'pause_integration_gate': gates[1],
        'additional_integration_gates': gates[2:],
        'integration_gate_sha256': {name: sha(ROOT / 'setup' / name) for name in gates},
        'selection_sha256': sha(selection_path), 'preparation_sha256': sha(preparation_path),
        'freeze_operator_sha256': sha(Path(__file__)),
        'method_document': 'harness-transfer-study-2026-10-06.md',
        'concurrency': {'new_cohort_cpu_peak': 2, 'workers': 2, 'memory_mb': 12288, 'other_pool_cpus': 6},
        'disk_reserve_gib': 60, 'automatic_retries': 0,
        'stop_admission_at_utc': '2026-10-06T14:14:06+00:00',
        'cloud_stop_at_utc': '2026-10-06T14:24:06+00:00',
        'contrasts': [{'control': 'native', 'treatment': 'sg_v2', 'hypothesis': 'whole_framework'},
                      {'control': 'mini_swe', 'treatment': 'sg_v2', 'hypothesis': 'external_harness'},
                      {'control': 'longhorizon', 'treatment': 'sg_v2', 'hypothesis': 'external_harness'}],
        'disclosures': [
            'Three task IDs, deliberate metadata selection, no training-contamination guarantee',
            'Original preparation attempt rejected prior-registration overlap before oracle or model calls',
            'LongHorizon unmodified upstream loop, CLI Hermes custom backend, writable tools for all roles; no Claude-specific readonly guard or GUI',
            'LongHorizon dev02 adds failure handoff classification to smoke dev01; seven local boundary tests passed',
            'Six manager rounds differ from six SG executor episodes; shared physical/time caps are the comparable budgets',
            'Existing frozen cohorts unchanged; transport/binding/snapshot fixes shared by all conditions here',
            'All failures retained; no task replacements or best-of-N retries; no post-result tuning in this cohort',
        ],
    }
    from run_registered import check_environment, check_sources
    check_sources(CANDIDATE, reg, ROOT / 'upstream/terminal-bench-2-1')
    check_environment(ROOT, ROOT / 'upstream/terminal-bench-2-1', environment)
    path = CANDIDATE / 'experiments/public_benchmarks' / ('registration-' + LABEL + '.json')
    save(path, reg)
    shutil.copyfile(path, ROOT / 'setup' / path.name)
    archive = ROOT / 'setup' / ('sg-' + LABEL + '.tar.gz')
    with tarfile.open(archive, 'x:gz') as bundle:
        bundle.add(CANDIDATE, arcname='supergoal-runtime')
    receipt = {'registration_sha256': sha(path), 'archive_sha256': sha(archive),
               'candidate': str(CANDIDATE), 'sources': len(sources), 'planned_cells': len(reg['planned_order']),
               'reference_passed_task_ids': [t['task_id'] for t in environment['tasks']],
               'environment_unavailable_task_ids': unavailable, 'model_calls': 0}
    save(ROOT / 'setup' / (LABEL + '-freeze.json'), receipt)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
