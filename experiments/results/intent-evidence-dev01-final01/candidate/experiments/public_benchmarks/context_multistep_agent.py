"""A 2x2 test of actual history handoff and public-requirement replay.

The independent auditor always sees the full public contract in all arms.
No official grading feedback is added to history or requirements.
"""
import copy
import hashlib
import json

from checkpoint_agent import CheckpointStudy
from context_policy import CONDITIONS, context_observation, fingerprint, public_prompt
from grader_history import conceal, restore_before_cancellation
from harbor_agent import dump
from multistep_agent import MultiStepStudy


class ContextCheckpoint(CheckpointStudy):
    def __init__(self, *args, incoming_history=None, audit_instruction, condition, **kwargs):
        self.incoming_history = copy.deepcopy(incoming_history)
        self.audit_instruction = audit_instruction
        self.context_condition = condition
        self.last_executor_history = None
        self.executor_contexts = []
        super().__init__(*args, arm='sg_v2', **kwargs)

    async def episode(self, prompt, proxy, *, role, container, history=None, max_calls=None):
        if role == 'executor':
            first = not self.executor_contexts
            if first and history is None:
                history = copy.deepcopy(self.incoming_history)
            self.executor_contexts.append({'first_executor_in_checkpoint': first,
                                           'history_messages': len(history or []),
                                           'history_sha256': fingerprint(history) if history else None,
                                           'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
        result = await super().episode(prompt, proxy, role=role, container=container,
                                       history=history, max_calls=max_calls)
        if role == 'executor' and isinstance(result.get('messages'), list):
            self.last_executor_history = copy.deepcopy(result['messages'])
        return result

    async def review(self, instruction, answer, proxy):
        return await super().review(self.audit_instruction, answer, proxy)

    async def run(self, instruction, environment, context):
        try:
            await super().run(instruction, environment, context)
        finally:
            path = self.control / 'report.json'
            if path.exists():
                report = json.loads(path.read_text())
                report.update(arm=self.context_condition, executor_contexts=self.executor_contexts,
                              audit_contract_sha256=hashlib.sha256(self.audit_instruction.encode()).hexdigest(),
                              audit_contract_policy='all public requirements in every condition')
                dump(path, report)
            if getattr(context, 'metadata', None):
                context.metadata['study_arm'] = self.context_condition


class ContextMultiStepStudy(MultiStepStudy):
    def __init__(self, *args, arm='carry_ledger', **kwargs):
        self.carry_history, self.replay_requirements = CONDITIONS[arm]
        self.last_executor_history = None
        super().__init__(*args, arm='sg_v2', **kwargs)
        self.arm = arm
        self.ledger.update(arm=arm, history_policy=arm, public_requirements=[],
                           acceptance_policy='Full cumulative public requirements for all arms',
                           scope='Development context handoff, not autonomous phase discovery')
        dump(self.control / 'problem.json', self.ledger)

    def version(self):
        return 'context-handoff-dev09'

    async def run(self, instruction, environment, context):
        index = len(self.ledger['steps']) + 1
        backup = self.control / f'grader-history-{index}.json'
        boundary = {'checkpoint': index, 'status': 'preparing'}
        self.ledger.setdefault('grader_history_boundaries', []).append(boundary)
        dump(self.control / 'problem.json', self.ledger)
        try:
            boundary.update(await conceal(environment, backup))
            dump(self.control / 'problem.json', self.ledger)
            await super().run(instruction, environment, context)
        finally:
            try:
                if backup.exists():
                    boundary['restoration'] = await restore_before_cancellation(environment, backup)
            except BaseException as exc:
                boundary.update(status='restoration_failed', error=type(exc).__name__ + ': ' + str(exc))
                raise
            finally:
                dump(self.control / 'problem.json', self.ledger)

    def make_child(self, remaining):
        incoming = self.last_executor_history if self.carry_history else None
        audit = public_prompt(self.instructions, replay=True)
        return ContextCheckpoint(self.study_logs_dir, condition=self.arm, incoming_history=incoming,
                                 audit_instruction=audit, max_requests=min(remaining, self.max_step_requests),
                                 max_episodes=6, budget_seconds=self.budget_seconds)

    def checkpoint_prompt(self, index, instruction):
        incoming = self.last_executor_history if self.carry_history else None
        self.ledger['public_requirements'] = list(self.instructions)
        self.ledger['steps'][-1]['context_input'] = context_observation(self.arm, self.instructions, incoming)
        dump(self.control / 'problem.json', self.ledger)
        return public_prompt(self.instructions, replay=self.replay_requirements)

    def child_finished(self, child, row):
        self.last_executor_history = child.last_executor_history
        row['executor_contexts'] = child.executor_contexts
        row['outgoing_history_messages'] = len(self.last_executor_history or [])
        row['outgoing_history_sha256'] = fingerprint(self.last_executor_history) if self.last_executor_history else None
