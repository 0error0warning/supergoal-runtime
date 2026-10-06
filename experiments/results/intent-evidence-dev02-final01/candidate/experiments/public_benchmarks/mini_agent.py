"""Harbor integration for real upstream mini-SWE-agent, not a reimplementation."""
import json

from harbor_agent import HERE, ROOT, HermesSupergoalStudy, dump


class MiniSweStudy(HermesSupergoalStudy):
    def __init__(self, *args, arm='mini_swe', **kwargs):
        if arm != 'mini_swe':
            raise ValueError('mini_swe condition required')
        super().__init__(*args, arm='native', **kwargs)

    def episode_command(self):
        return [str(ROOT / 'mini-venv/bin/python'), str(HERE / 'mini_episode.py')]

    def version(self):
        return 'mini-swe-agent-2.4.6-04d809c-transport01'

    async def run(self, instruction, environment, context):
        try:
            await super().run(instruction, environment, context)
        finally:
            path = self.control / 'report.json'
            if path.exists():
                report = json.loads(path.read_text())
                report.update(arm='mini_swe', executor='upstream mini-SWE-agent', hermes_version=None,
                              upstream_commit='04d809ceab9df28f9adaed044884180159172930')
                dump(path, report)
            if getattr(context, 'metadata', None):
                context.metadata['study_arm'] = 'mini_swe'
