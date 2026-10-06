"""One real Hermes task inside the experiment namespace; no production imports."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import yaml


def main():
    provider = json.loads(Path('/run/model.json').read_text())
    home = Path(os.environ['HERMES_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    config = {
        'model': {'default': provider['model'], 'provider': 'custom:Devin',
                  'base_url': provider['base_url']},
        'custom_providers': [{'name': 'Devin', **provider}],
        'fallback_providers': [],
        'plugins': {'enabled': []},
        'terminal': {'backend': 'local', 'cwd': '/workspace', 'timeout': 30},
        'memory': {'memory_enabled': False, 'user_profile_enabled': False},
        'compression': {'enabled': False},
        'agent': {'max_turns': 48},
        'mcp_servers': {},
    }
    (home / 'config.yaml').write_text(yaml.safe_dump(config))
    os.chdir('/workspace')
    sys.path.insert(0, '/opt/hermes')
    from run_agent import AIAgent
    started = time.monotonic()
    agent = AIAgent(
        model=provider['model'], base_url=provider['base_url'],
        api_key=provider['api_key'], provider='custom:Devin',
        api_mode=provider['api_mode'], requested_provider='custom:Devin',
        enabled_toolsets=['terminal', 'file'], max_iterations=8,
        max_tokens=3000, run_budget_seconds=180, quiet_mode=True,
        skip_memory=True, skip_context_files=True, skip_background_review=True,
        session_id='supergoal-lab-smoke', fallback_model=None,
    )
    result = agent.run_conversation(
        'Create result.json in the current workspace containing exactly '
        '{"message": "hermes-swe2-live", "value": 6}. Read it back to verify, then finish.'
    )
    Path('/records/response.json').write_text(json.dumps(result, default=str))
    print(json.dumps({'probe': 'hermes-real-tool-loop', 'seconds': time.monotonic()-started,
                      'completed': result.get('completed'), 'error': result.get('error'),
                      'api_calls': result.get('api_calls'),
                      'tools': sorted(agent.valid_tool_names),
                      'artifact': json.loads(Path('result.json').read_text())
                      if Path('result.json').exists() else None}), flush=True)
    agent.close()


if __name__ == '__main__':
    main()
