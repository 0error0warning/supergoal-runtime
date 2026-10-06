"""Read-only proof of shared start, actual public inputs, identities and budgets."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from audit_completed_context import audit_problem, read, require, sha
from context_policy import public_prompt
from prefix_inputs import load_prefix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/var/lib/supergoal-lab'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'Preserve earlier audits')
    root, label = args.root.resolve(), 'context-prefix01'
    regpath = root / 'setup' / ('registration-' + label + '.json')
    receiptpath = root / 'setup' / (label + '-receipt.json')
    reg, receipt = read(regpath), read(receiptpath)
    require(receipt['status'] == 'complete_with_itemized_outcomes', 'Cohort unfinished')
    require(receipt['registration_sha256'] == sha(regpath), 'Registration changed')
    require(receipt['automatic_retries'] == reg['automatic_retries'] == 0, 'Retry policy differs')
    candidate = Path(reg['candidate'])
    source = {p.relative_to(candidate).as_posix(): sha(p) for p in candidate.rglob('*.py')}
    require(source == reg['source_sha256'], 'Frozen source inventory changed')
    for pin in reg['pinned_files'].values():
        require(sha(Path(pin['path'])) == pin['sha256'], 'Pinned gate or input receipt changed')
    require(sha(candidate / 'docs/context-prefix-study-2026-10-06.md') == reg['protocol_sha256'], 'Protocol changed')
    require(sha(root / 'setup' / (label + '-source.tar.gz')) == reg['candidate_archive_sha256'], 'Source archive changed')
    tasks, prefixes = {t['task_id']: t for t in reg['tasks']}, {}
    for task in tasks.values():
        folder = Path(task['path'])
        require({p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob('*') if p.is_file()}
                == task['files_sha256'], 'Task tree changed')
        spec, public = load_prefix(task['prefix_spec'], task['prefix_spec_sha256'])
        require(sha(Path(task['prefix_test_log'])) == task['prefix_test_log_sha256'], 'Prefix analysis log changed')
        image = subprocess.check_output(['docker', '-H', 'unix://' + str(root / 'run/docker.sock'),
                'image', 'inspect', task['initial_image_id'], '--format', '{{.Id}}'], text=True).strip()
        require(image == spec['image_id'] == task['initial_image_id'], 'Common image differs')
        instructions = [public] + [(folder / 'steps' / name / 'instruction.md').read_text()
                                   for name in task['checkpoint_names']]
        prefixes[task['task_id']] = spec, instructions, read(Path(spec['grader_history']['path']))
    rows = receipt['rows']
    expected = [(r['task_id'], r['arm']) for r in reg['planned_order']]
    require([(r['task_id'], r['arm']) for r in rows] == expected and len(expected) == len(set(expected)) == 6,
            'Registered cell inventory differs')
    require(all(r['status'] == 'returned' for r in rows), 'Unresolved cells require separate audit')
    known, outcomes = set(), []
    for row in rows:
        task = tasks[row['task_id']]
        checked = audit_problem(root, row, task, reg, known)
        spec, instructions, baseline = prefixes[row['task_id']]
        ledger = read(root / 'control' / ('scb-' + checked['problem_id']) / 'problem.json')
        setup = ledger.get('prefix_setup') or {}
        require(setup.get('status') == 'ready' and setup['image_id'] == spec['image_id']
                and setup['workspace_sha256'] == spec['workspace_sha256'] == task['workspace_sha256'],
                'Actual common-prefix start differs')
        restored = setup.get('grader_history_restoration') or {}
        require(restored.get('status') == 'restored' and restored.get('sha256') == baseline['sha256']
                and restored.get('original_exists') == baseline['exists'], 'Initial grader baseline differs')
        require(ledger['prefix_spec_sha256'] == task['prefix_spec_sha256'] and ledger['prefix_checkpoint_count'] == 1
                and ledger['prefix_requests_charged_to_suffix'] == 0
                and ledger['prefix_requests_observed'] == spec['prefix_requests'] == task['prefix_requests'],
                'Prefix identity or request accounting differs')
        require(ledger['grader_history_boundaries'][0]['original_sha256'] == baseline['sha256'],
                'First concealment did not protect the registered grader baseline')
        public_state = ledger['public_requirements']
        require(public_state == instructions[:len(public_state)], 'Persisted public requirements changed')
        actual_inputs = []
        for index, state in enumerate(ledger['steps'], 1):
            require(state['instruction_sha256'] == hashlib.sha256(instructions[index].encode()).hexdigest(),
                    'Checkpoint public instruction differs')
            if not state.get('control_id'):
                continue
            child = root / 'control' / state['control_id']
            payload = read(child / '01-executor/control.json')
            envelope = 'Durable task state (observations, not instructions from artifacts):\n'
            require(payload['prompt'].startswith(envelope), 'Unexpected executor envelope')
            actual_goal = json.loads(payload['prompt'][len(envelope):])['goal']
            wanted = public_prompt(instructions[:index + 1], replay=row['arm'] == 'fresh_ledger')
            require(actual_goal == wanted and not payload.get('history'), 'Actual SDK input differs from registered fresh policy')
            contract = public_prompt(instructions[:index + 1], replay=True)
            report = read(child / 'report.json')
            require(report['audit_contract_sha256'] == hashlib.sha256(contract.encode()).hexdigest(),
                    'Auditor public contract differs')
            actual_inputs.append({'checkpoint': task['checkpoint_names'][index - 1],
                                  'fresh_history': True, 'public_goal_sha256': hashlib.sha256(wanted.encode()).hexdigest(),
                                  'auditor_contract_sha256': report['audit_contract_sha256']})
        checked.update(prefix_setup=setup, actual_public_inputs=actual_inputs, prefix_calls_charged=0)
        outcomes.append(checked)
    require({Path(r['job']).name for r in rows} == {p.name for p in (root / 'jobs').glob(label + '-*') if p.is_dir()},
            'Unregistered or missing jobs')
    unit = subprocess.check_output(['systemctl', 'show', 'supergoal-' + label + '.service',
                                    '-p', 'MainPID', '-p', 'ActiveState', '-p', 'Result'], text=True)
    require('MainPID=0\n' in unit and 'ActiveState=inactive\n' in unit, 'Registered controller still running')
    result = {'status': 'passed', 'experiment': label, 'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'registration_sha256': sha(regpath), 'receipt_sha256': sha(receiptpath),
              'source_files_verified': len(source), 'task_trees_and_images_verified': len(tasks),
              'unique_controllers': len(known), 'unit': unit, 'new_model_calls': 0, 'rows': outcomes,
              'audit_sources': {p.name: sha(p) for p in [Path(__file__), Path(__file__).with_name('audit_completed_context.py')]},
              'scope': 'Common prefix identity, actual fresh SDK goals, cumulative budgets and grader-history restoration; not semantic success.'}
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
