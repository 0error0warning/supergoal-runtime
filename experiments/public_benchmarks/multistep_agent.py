"""SCBench checkpoint lifecycle with one shared problem request ceiling.

Harbor supplies each official step and preserves its workspace. Only public
instructions are retained between steps; official verifier feedback is never
read or added to the solver prompt. Each checkpoint has a new child controller,
avoiding duplicate goal IDs, stale completion, and overwritten model journals.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from harbor.agents.base import BaseAgent
from harbor_agent import ROOT, dump
from checkpoint_agent import CheckpointStudy, CheckpointMiniStudy


def reserved_requests(children):
    total = 0
    for child in children:
        path = ROOT / 'control' / child / 'request-journal.json'
        if path.exists():
            total += len(json.loads(path.read_text())['records'])
    return total


class MultiStepStudy(BaseAgent):
    def __init__(self, logs_dir, *, arm='sg_v2', max_problem_requests=384,
                 max_step_requests=128, max_checkpoints=5, budget_seconds=1800, **kwargs):
        if arm not in {'native', 'sg_v2', 'mini_swe'}:
            raise ValueError('Unregistered multi-step arm')
        super().__init__(logs_dir=logs_dir, **kwargs)
        self.arm, self.study_logs_dir = arm, logs_dir
        self.max_problem_requests, self.max_step_requests = max_problem_requests, max_step_requests
        self.max_checkpoints, self.budget_seconds = max_checkpoints, budget_seconds
        self.uid = uuid.uuid4().hex
        self.control = ROOT / 'control' / ('scb-' + self.uid)
        self.control.mkdir(mode=0o700)
        self.ledger = {'arm': arm, 'problem_id': self.uid, 'children': [], 'steps': [],
                       'max_problem_requests': max_problem_requests, 'status': 'ready',
                       'history_policy': 'cumulative public requirements, new executor session per checkpoint',
                       'verifier_feedback_visible': False}
        self.instructions = []
        dump(self.control / 'problem.json', self.ledger)

    @staticmethod
    def name():
        return 'supergoal-scb-study'

    def version(self):
        return 'scb-transfer01-checkpoint08'

    async def setup(self, environment):
        # Each child probes the current step environment just before execution.
        pass

    def make_child(self, remaining):
        cls = CheckpointMiniStudy if self.arm == 'mini_swe' else CheckpointStudy
        return cls(self.study_logs_dir, arm=self.arm, max_requests=min(remaining, self.max_step_requests),
                   max_episodes=6, budget_seconds=self.budget_seconds)

    def checkpoint_prompt(self, index, instruction):
        if index == 1:
            return instruction
        return ('Continue extending the existing workspace for the current checkpoint. '
                'Earlier requirements remain in force unless the current instruction changes them.\n'
                + json.dumps({'earlier_public_requirements': self.instructions[:-1],
                              'current_public_requirement': instruction}, ensure_ascii=False))

    def child_finished(self, child, row):
        pass

    async def run(self, instruction, environment, context):
        index = len(self.ledger['steps']) + 1
        if index > self.max_checkpoints:
            raise ValueError('More checkpoints than registered')
        used = reserved_requests(self.ledger['children'])
        remaining = self.max_problem_requests - used
        row = {'checkpoint': index, 'instruction_sha256': hashlib.sha256(instruction.encode()).hexdigest(),
               'requests_before': used, 'status': 'running'}
        self.ledger['steps'].append(row)
        self.instructions.append(instruction)
        if remaining <= 0:
            row['status'] = 'problem_budget_exhausted'
            context.metadata = {'study_arm': self.arm, 'study_status': row['status'],
                                'problem_id': self.uid, 'checkpoint': index, 'physical_requests': 0}
            self.ledger['status'] = row['status']
            dump(self.control / 'problem.json', self.ledger)
            return
        child = self.make_child(remaining)
        self.ledger['children'].append(child.uid)
        row['control_id'] = child.uid
        self.ledger['status'] = 'running'
        dump(self.control / 'problem.json', self.ledger)
        # An explicit common adaptation: all three arms get the previous public
        # requirements, so fresh sessions do not silently lose the task contract.
        prompt = self.checkpoint_prompt(index, instruction)
        try:
            await child.setup(environment)
            await child.run(prompt, environment, context)
            row['status'] = 'returned'
        except BaseException as exc:
            row.update(status='execution_exception', error=type(exc).__name__ + ': ' + str(exc)[:1200])
            raise
        finally:
            self.child_finished(child, row)
            total = reserved_requests(self.ledger['children'])
            row['requests_after'] = total
            row['physical_requests'] = total - used
            self.ledger.update(status='checkpoint_boundary', reserved_requests=total)
            dump(self.control / 'problem.json', self.ledger)
            context.metadata = {**(getattr(context, 'metadata', None) or {}),
                                'problem_id': self.uid, 'checkpoint': index,
                                'problem_requests_total': total, 'multistep_adapter': self.version()}
