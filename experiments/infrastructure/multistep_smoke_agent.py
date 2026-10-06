"""Probe-only assertion of Harbor's hidden-verifier cleanup boundary."""
from multistep_agent import MultiStepStudy


class CheckedMultiStep(MultiStepStudy):
    async def run(self, instruction, environment, context):
        if self.ledger['steps']:
            result = await environment.exec('test ! -e /tests/SG_HIDDEN_VERIFIER_CANARY', user='root')
            if result.return_code:
                raise RuntimeError('Previous hidden verifier survived into the next solver step')
        await super().run(instruction, environment, context)
        context.metadata['previous_hidden_verifier_absent'] = True

