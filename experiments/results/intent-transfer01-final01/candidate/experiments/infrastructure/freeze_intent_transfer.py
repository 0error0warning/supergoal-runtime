"""Final held-out registration; no solver can begin before these pins exist."""
import datetime
import hashlib
import json
from pathlib import Path


ROOT = Path('/var/lib/supergoal-lab')
CANDIDATE = ROOT / 'candidates/intent-transfer01'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    dev = json.loads((ROOT / 'setup/intent-evidence-dev02-receipt.json').read_text())
    if dev['status'] != 'complete' or len(dev['rows']) != 3:
        raise ValueError('Development integration gate unfinished')
    for row in dev['rows']:
        if row.get('exception') or not row.get('raw_rewards'):
            raise ValueError('Development execution or delivery failure needs audit')
        if row['arm'] == 'evidence':
            uid = row['agent_result']['metadata']['control_id']
            report = json.loads((ROOT / 'control' / uid / 'report.json').read_text())
            if report['goal_brief']['authority'] != 'advisory_model_interpretation':
                raise ValueError('Goal interpretation has not passed its integration gate')
            if not report['coverage_audits'] or any(a.get('coverage_error') for a in report['coverage_audits']):
                raise ValueError('Structured observation coverage has not passed its integration gate')
    environment = json.loads((ROOT / 'setup/registration-intent-environment01.json').read_text())
    rubric = ROOT / 'research-grading/intent-transfer01/rubric-lock.json'
    if not rubric.exists():
        raise ValueError('Independent research rubrics must be frozen first')
    arms = ['native', 'repeat_goal', 'sg_v2', 'evidence']
    tasks = environment['tasks']
    plan = [{'index': wave * len(tasks) + index, 'task_id': task['task_id'], 'arm': arms[(index + wave) % 4]}
            for wave in range(4) for index, task in enumerate(tasks)]
    reg = {'schema': 1, 'experiment': 'intent-transfer01',
           'registered_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'candidate': str(CANDIDATE), 'kind': 'held_out_after_two_development_candidates',
           'source_sha256': {p.relative_to(CANDIDATE).as_posix(): sha(p) for p in sorted(CANDIDATE.rglob('*'))
                             if p.is_file() and '__pycache__' not in p.parts},
           'tasks': tasks, 'planned_order': plan, 'arms': arms, 'max_requests': 128,
           'max_episodes': 6, 'concurrency': 4, 'repeats_per_task_arm': 1,
           'model': 'devin/swe-2', 'max_output_tokens_per_request': 6000,
           'host': 'supergoal-gcp', 'hardware': 'n1-highmem-8', 'harbor': '0.24.0',
           'hermes_source': 'hermes-v0.21.3-251bedc-mt2',
           'reference_receipt': str(ROOT / 'setup/intent-environment01-oracles-receipt.json'),
           'selection_sha256': sha(ROOT / 'setup/intent-transfer01-selection.json'),
           'research_rubric_lock_sha256': sha(rubric),
           'research_diagnostic_scope': 'request-only model rubric and sampled source support; not official DR3-Eval or human truth',
           'research_delivery_reward_is_not_success': True,
           'absolute_stop': '2026-10-06T14:24:06+00:00', 'automatic_trial_retries': 0,
           'hidden_feedback_visible_to_online_roles': False,
           'source_changes_after_development': ['Workflow envelope exposes opt-in guidance',
               'Kernel requires explicit retryable=true for unknown continuation; all candidate audit outcomes already include this boolean',
               'Independent grading/registration operators and tests; no task-specific algorithm branches'],
           'prior_development_labels': ['intent-evidence-dev01', 'intent-evidence-dev02']}
    path = ROOT / 'setup/registration-intent-transfer01.json'
    with path.open('x') as handle:
        json.dump(reg, handle, indent=2)
    print(json.dumps({'registration': str(path), 'sha256': sha(path), 'planned_trials': len(plan), 'model_trials_started': 0}))


if __name__ == '__main__':
    main()
