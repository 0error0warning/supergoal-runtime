"""One fresh Hermes process per episode, sharing the arm's persisted task state.

Uses the real AIAgent, native GoalManager and v1 PluginContext/turn-controller ABI.
The v2 kernel is an experimental headless adapter, not a gateway deployment.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import yaml


def write(path, value):
    target = Path(path)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(value, default=str, ensure_ascii=False))
    temp.replace(target)


def main():
    task = json.loads(Path('/task/task.json').read_text())
    provider = json.loads(Path('/task/provider.json').read_text())
    control = json.loads(Path('/task/control.json').read_text())
    arm, session_id = control['arm'], control['run_id']
    home = Path(os.environ['HERMES_HOME'])
    plugin = home / 'plugins/lab-observer'
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / 'plugin.yaml').write_text('name: lab-observer\nversion: 0.1.0\ndescription: Local study observation\n')
    (plugin / '__init__.py').write_text('from study_hooks import register\n')
    config = {'model': {'default': provider['model'], 'provider': 'custom:Devin', 'base_url': provider['base_url']},
              'custom_providers': [{'name': 'Devin', **provider}], 'fallback_providers': [],
              'plugins': {'enabled': ['lab-observer']},
              'terminal': {'backend': 'local', 'cwd': '/workspace', 'timeout': 30},
              'memory': {'memory_enabled': False, 'user_profile_enabled': False},
              'compression': {'enabled': False}, 'agent': {'max_turns': 48}, 'mcp_servers': {},
              'auxiliary': {'goal_judge': {'provider': 'custom:Devin', 'model': 'devin/swe-2', 'timeout': 18, 'max_tokens': 1800}}}
    (home / 'config.yaml').write_text(yaml.safe_dump(config))
    sys.path.insert(0, '/opt/hermes')
    sys.path.insert(0, '/opt/sg')
    from run_agent import AIAgent
    agent = AIAgent(model=provider['model'], base_url=provider['base_url'], api_key=provider['api_key'],
                    provider='custom:Devin', requested_provider='custom:Devin', api_mode='codex_responses',
                    enabled_toolsets=['terminal', 'file'], max_iterations=control['remaining_calls'],
                    max_tokens=3000, run_budget_seconds=min(900, control['remaining_seconds']),
                    quiet_mode=True, skip_memory=True, skip_context_files=True, skip_background_review=True,
                    session_id=session_id, fallback_model=None)
    import study_hooks
    if study_hooks.CONTEXT is None:
        raise RuntimeError('Hermes did not discover the observation plugin')
    history_path = Path('/records/history.json')
    history = json.loads(history_path.read_text()) if history_path.exists() else None
    previous_path = Path('/records/episode.json')
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    prompt = previous['next_prompt'] if previous else task['prompt']
    native = legacy = None
    if arm.startswith('sg_v2'):
        # The authoritative kernel is outside the tool sandbox. Its prepared
        # context is read-only; neither tools nor model can edit its SQLite DB.
        prompt = control['prompt']
    elif arm == 'native_goal':
        from hermes_cli.goals import GoalManager
        native = GoalManager(session_id, default_max_turns=6)
        if not previous:
            native.set(task['prompt'], max_turns=6)
    elif arm == 'sg_v1':
        from supergoal_runtime.plugin import _PluginRuntime
        legacy = _PluginRuntime(study_hooks.CONTEXT)
        study_hooks.CONTEXT.register_hook('pre_tool_call', legacy.tool_hooks.pre_tool_call)
        study_hooks.CONTEXT.register_hook('post_tool_call', legacy.tool_hooks.post_tool_call)
        study_hooks.CONTEXT.register_turn_controller('supergoal-runtime', legacy.after_turn, priority=100, continuation_provider=legacy)
        if not previous:
            _, envelope = legacy.manager.start_for_command(session_id, task['prompt'], max_turns=6)
            if not legacy.claim_continuation(session_id=session_id, token=envelope['token'], state_version=envelope['state_version']):
                raise RuntimeError('v1 start continuation not claimable')
            prompt = envelope['prompt']
        elif previous.get('directive', {}).get('continuation_token'):
            directive = previous['directive']
            if not legacy.claim_continuation(session_id=session_id, token=directive['continuation_token'], state_version=directive.get('state_version')):
                raise RuntimeError('v1 continuation not claimable')
    started = time.monotonic()
    result = agent.run_conversation(prompt, conversation_history=history)
    write('/records/history.json', result.get('messages', []))
    write('/records/raw-turn-' + str(control['episode']) + '.json', result)
    final = str(result.get('final_response') or '')
    status = 'completed' if result.get('completed') else 'executor_incomplete'
    next_prompt, decision = '', None
    if arm.startswith('sg_v2'):
        status = 'candidate_ready'
        next_prompt = 'Continue the assigned task from the current workspace. Complete and verify remaining requirements.'
    elif native:
        from agent.auxiliary_client import scoped_runtime_main
        with scoped_runtime_main({'provider': 'custom:Devin', 'base_url': provider['base_url'], 'api_key': provider['api_key'], 'api_mode': 'codex_responses', 'model': 'devin/swe-2'}):
            decision = native.evaluate_after_turn(final, user_initiated=control['episode'] == 1)
        status = 'active' if decision.get('should_continue') else decision.get('status', 'no_directive')
        next_prompt = decision.get('continuation_prompt') or ''
    elif legacy:
        from hermes_cli.plugins import TurnControlContext, invoke_turn_controllers
        from agent.auxiliary_client import scoped_runtime_main
        ctx = TurnControlContext('cli', session_id, 'cli', None, session_id,
                                 result.get('turn_id') or f'episode-{control["episode"]}', prompt, final, False, [])
        with scoped_runtime_main({'provider': 'custom:Devin', 'base_url': provider['base_url'], 'api_key': provider['api_key'], 'api_mode': 'codex_responses', 'model': 'devin/swe-2'}):
            raw_directive = asyncio.run(invoke_turn_controllers(ctx))
        decision = asdict(raw_directive) if raw_directive else {}
        current = legacy.manager.load_state_for_session(session_id)
        if current is None or current.turns_used != control['episode']:
            raise RuntimeError('Invalid v1 integration: automatic turn was not accounted')
        status = {'continue': 'active', 'done': 'succeeded', 'pause': 'paused'}.get(decision.get('action'), 'no_directive')
        next_prompt = decision.get('continuation_prompt') or ''
    episode = {'episode': control['episode'], 'status': status, 'next_prompt': next_prompt,
               'api_calls': result.get('api_calls'), 'completed': result.get('completed'),
               'error': result.get('error'), 'seconds': time.monotonic()-started,
               'final_response': final, 'directive': decision,
               'pid': os.getpid(), 'process_instance': time.time_ns()}
    write('/records/episode.json', episode)
    print(json.dumps({k: episode[k] for k in ('episode', 'status', 'api_calls', 'seconds')}), flush=True)
    agent.close()


if __name__ == '__main__':
    main()
