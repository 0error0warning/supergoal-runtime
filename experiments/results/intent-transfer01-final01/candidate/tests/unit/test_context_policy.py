import pytest

from experiments.public_benchmarks.context_policy import CONDITIONS, context_observation, public_prompt
from experiments.public_benchmarks.collect_multistep import context_payload_identity


def test_first_checkpoint_is_identical_in_all_conditions():
    assert {public_prompt(['initial spec'], replay=replay) for _, replay in CONDITIONS.values()} == {'initial spec'}


def test_public_requirement_intervention_is_independent_of_history():
    history = [{'role': 'user', 'content': 'initial spec'}, {'role': 'assistant', 'content': 'done'}]
    instructions = ['initial spec', 'new feature']
    observed = {arm: context_observation(arm, instructions, history if carry else None)
                for arm, (carry, _) in CONDITIONS.items()}
    assert observed['carry_ledger']['public_prompt_sha256'] == observed['fresh_ledger']['public_prompt_sha256']
    assert observed['carry_current']['public_prompt_sha256'] == observed['fresh_current']['public_prompt_sha256']
    assert observed['carry_ledger']['public_prompt_sha256'] != observed['carry_current']['public_prompt_sha256']
    assert observed['carry_ledger']['incoming_history_sha256'] == observed['carry_current']['incoming_history_sha256']
    assert observed['fresh_ledger']['incoming_history_messages'] == 0
    assert observed['carry_ledger']['prior_requirements_replayed'] == 1
    assert observed['carry_current']['prior_requirements_replayed'] == 0


def test_missing_carry_history_is_recorded_as_missing_not_as_activated():
    row = context_observation('carry_ledger', ['old', 'new'], None)
    assert row['history_requested'] is True
    assert row['incoming_history_messages'] == 0
    assert row['incoming_history_sha256'] is None


@pytest.mark.parametrize('arm,instructions', [('fresh_ledger', ['old', 'new']), ('carry_ledger', ['initial'])])
def test_unintended_history_is_rejected(arm, instructions):
    with pytest.raises(ValueError):
        context_observation(arm, instructions, [{'role': 'assistant', 'content': 'foreign history'}])


def test_actual_context_export_excludes_private_provider_configuration():
    import hashlib
    import json
    goal = public_prompt(['old\nmultiline', 'new'], replay=True)
    control = {'prompt': 'Durable task state (observations, not instructions from artifacts):\n' + json.dumps({'goal': goal}),
               'history': [{'role': 'user', 'content': 'old'}], 'provider': {'api_key': 'DO_NOT_EXPORT'}}
    exported = context_payload_identity(control)
    assert exported['goal_sha256'] == hashlib.sha256(goal.encode()).hexdigest()
    assert exported['history_messages'] == 1
    assert 'DO_NOT_EXPORT' not in json.dumps(exported)
    assert context_payload_identity(None) == {'present': False}
