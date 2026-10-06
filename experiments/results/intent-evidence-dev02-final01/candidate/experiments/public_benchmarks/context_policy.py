"""Cross-checkpoint context interventions, separate from acceptance policy."""
import hashlib
import json


CONDITIONS = {
    'carry_ledger': (True, True),
    'carry_current': (True, False),
    'fresh_ledger': (False, True),
    'fresh_current': (False, False),
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def public_prompt(instructions, *, replay):
    if not instructions or not all(isinstance(x, str) and x for x in instructions):
        raise ValueError('Nonempty public instructions required')
    if len(instructions) == 1:
        return instructions[0]
    text = ('Continue extending the existing workspace for the current checkpoint. '
            'Earlier requirements remain in force unless the current instruction changes them.\n')
    body = {'current_public_requirement': instructions[-1]}
    if replay:
        body['earlier_public_requirements'] = instructions[:-1]
    return text + json.dumps(body, ensure_ascii=False)


def context_observation(condition, instructions, history):
    carry, replay = CONDITIONS[condition]
    previous = len(instructions) - 1
    if not previous and history:
        raise ValueError('Initial checkpoint must not contain prior model history')
    if not carry and history:
        raise ValueError('Fresh condition received prior model history')
    return {'condition': condition, 'prior_public_requirements': previous,
            'prior_requirements_replayed': previous if replay else 0,
            'history_requested': carry and previous > 0,
            'incoming_history_messages': len(history or []),
            'incoming_history_sha256': fingerprint(history) if history else None,
            'public_prompt_sha256': hashlib.sha256(public_prompt(instructions, replay=replay).encode()).hexdigest(),
            'public_requirements_sha256': fingerprint(instructions)}
