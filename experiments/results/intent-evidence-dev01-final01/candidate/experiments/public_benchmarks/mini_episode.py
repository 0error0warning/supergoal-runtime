"""Pinned upstream mini-SWE-agent loop, prompts, action parser and termination.

Only the SWE2 streaming transport and existing Harbor container are adapted.
No credentials or host filesystem tools are exposed to the model.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/var/lib/supergoal-lab')
UPSTREAM = ROOT / 'upstream/mini-swe-agent'
PIN = '04d809ceab9df28f9adaed044884180159172930'


def main():
    actual = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != PIN or subprocess.check_output(['git', '-C', str(UPSTREAM), 'status', '--porcelain'], text=True):
        raise RuntimeError('Pinned upstream mini source changed')
    os.environ['MSWEA_SILENT_STARTUP'] = '1'
    os.environ['MSWEA_GLOBAL_CONFIG_DIR'] = str(Path(os.environ['HOME']) / 'mini-home')
    sys.path.insert(0, str(UPSTREAM / 'src'))
    import yaml
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.docker import DockerEnvironment, DockerEnvironmentConfig
    from minisweagent.models.openrouter_response_model import OpenRouterResponseModel
    from minisweagent.models.utils.actions_toolcall_response import BASH_TOOL_RESPONSE_API

    control = json.loads(Path(sys.argv[1]).read_text())
    descriptor = json.loads(Path(os.environ['SUPERGOAL_HARBOR_DESCRIPTOR']).read_text())
    provider = control['provider']
    config = yaml.safe_load((UPSTREAM / 'src/minisweagent/config/mini.yaml').read_text())

    class SWE2ResponseModel(OpenRouterResponseModel):
        def _query(self, messages, **kwargs):
            # Codex-compatible streaming endpoint. Preserve upstream flattening,
            # function-call parsing, error messages and observation templates.
            instructions, inputs = [], []
            for message in messages:
                if message.get('role') == 'system':
                    instructions.extend(c['text'] for c in message['content'] if c.get('type') == 'input_text')
                else:
                    inputs.append(message)
            body = {'model': 'devin/swe-2', 'instructions': '\n'.join(instructions),
                    'input': inputs, 'tools': [BASH_TOOL_RESPONSE_API], 'stream': True,
                    'store': False, 'max_output_tokens': 6000}
            request = urllib.request.Request(provider['base_url'] + '/responses',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + provider['api_key']})
            with urllib.request.urlopen(request, timeout=90) as response:
                for line in response:
                    if line.startswith(b'data: '):
                        try:
                            event = json.loads(line[6:])
                        except ValueError:
                            continue
                        if event.get('type') in {'response.completed', 'response.incomplete', 'response.failed'}:
                            return event['response']
            raise RuntimeError('SWE2 stream ended without a terminal response')

    class ExistingContainer(DockerEnvironment):
        def __init__(self):
            self.config = DockerEnvironmentConfig(image='harbor-managed', cwd=control['cwd'],
                                                  timeout=120, env=config['environment']['env'])
            self.logger = logging.getLogger('mini-harbor')
            self.container_id = descriptor['container_id']

        def execute(self, action, cwd='', *, timeout=None):
            seconds = min(120, timeout or self.config.timeout)
            cmd = ['/usr/bin/docker', '--host', descriptor['docker_host'], 'exec',
                   '-u', descriptor['user'], '-w', cwd or self.config.cwd]
            for key, value in self.config.env.items():
                cmd.extend(['-e', key + '=' + value])
            cmd.extend([self.container_id, 'timeout', '--signal=TERM', '--kill-after=5s', str(seconds),
                        'bash', '-lc', action.get('command', '')])
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=seconds + 10,
                                        text=True, encoding='utf-8', errors='replace')
                output = {'output': result.stdout + result.stderr, 'returncode': result.returncode,
                          'exception_info': ''}
            except subprocess.TimeoutExpired:
                output = {'output': '', 'returncode': 124, 'exception_info': 'Container command timed out'}
            self._check_finished(output)
            return output

        def cleanup(self):
            # Harbor owns this container and runs the official verifier later.
            pass

    model_config = config['model']
    model_config.pop('model_kwargs', None)
    model = SWE2ResponseModel(model_name='devin/swe-2', cost_tracking='ignore_errors', **model_config)
    agent_config = config['agent']
    agent_config.pop('mode', None)
    agent_config.update(step_limit=control['max_calls'], cost_limit=0,
                        wall_time_limit_seconds=int(control['seconds']),
                        output_path=Path(control['output']).with_name('mini-trajectory.json'))
    agent = DefaultAgent(model, ExistingContainer(), **agent_config)
    started = time.monotonic()
    result = agent.run(control['prompt'])
    output = {'completed': result.get('exit_status') == 'Submitted',
              'final_response': result.get('submission', ''), 'mini_exit_status': result.get('exit_status'),
              'api_calls': agent.n_calls, 'messages': agent.messages,
              'research_episode_seconds': time.monotonic() - started, 'upstream_commit': PIN}
    Path(control['output']).write_text(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
