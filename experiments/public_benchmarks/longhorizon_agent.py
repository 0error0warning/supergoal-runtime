"""Pinned LongHorizon control loop with a real Hermes/SWE2 role backend.

The upstream manager, role prompts, report parsing, format repair and completion
gate run unchanged. This backend uses terminal/file tools in the Harbor task
container for every role, like the upstream Codex backend's shared workspace.
It does not implement the Claude backend's separate read-only filesystem guard.
It is a CLI-only integration, not an OSWorld or published leaderboard score.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from checkpoint_agent import SnapshotMixin
from harbor_agent import HermesSupergoalStudy, ROOT, dump
from model_proxy import ModelProxy


UPSTREAM = ROOT / 'upstream/longhorizon-harness'
PIN = 'a1dd930614972b92361c1b9cd6aac441a6db5a65'


def verify_upstream():
    actual = subprocess.check_output(['git', '-C', str(UPSTREAM), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(UPSTREAM), 'status', '--porcelain'], text=True)
    if actual != PIN or dirty:
        raise RuntimeError('Pinned LongHorizon upstream changed')
    source = str(UPSTREAM / 'src')
    if source not in sys.path:
        sys.path.insert(0, source)


class ContainerEnvironment:
    """Only task-container execution; the canonical ledger stays on the host."""

    def __init__(self, agent, environment):
        self.agent, self.environment = agent, environment
        self.staging_dir = agent.control / 'lh-staging'
        self.staging_dir.mkdir()
        self.remote_root = '/tmp/lh-harness-' + agent.uid

    def local_path(self, raw):
        path = Path(raw).resolve()
        if not path.is_relative_to(self.agent.control.resolve()):
            raise ValueError('LongHorizon transfer left the owned control directory')
        return path

    async def exec(self, command, timeout=30, tee_path=None):
        from lh_harness.types import ExecResult
        started = time.monotonic()
        result = await self.environment.exec(command, timeout_sec=timeout, user=self.agent.user)
        if tee_path:
            target = self.local_path(tee_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result.stdout, encoding='utf-8')
        return ExecResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.return_code,
                          duration_ms=int((time.monotonic() - started) * 1000))

    async def upload(self, local_path, remote_path):
        if not remote_path.startswith(self.remote_root + '/') or '..' in Path(remote_path).parts:
            raise ValueError('Unexpected remote harness trace path')
        await self.agent.docker('cp', str(self.local_path(local_path)), self.agent.container + ':' + remote_path)

    async def download(self, remote_path, local_path):
        target = self.local_path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.agent.docker('cp', self.agent.container + ':' + remote_path, str(target))

    async def screenshot(self):
        raise NotImplementedError('Registered terminal-only environment has no GUI')


class HermesRole:
    def __init__(self, agent, proxy, role):
        self.agent, self.proxy, self.role = agent, proxy, role

    async def run_episode(self, prompt, env, budget, live_trajectory_path=None):
        from lh_harness.types import EpisodeResult
        started = time.monotonic()
        if self.role.startswith('gui_'):
            return EpisodeResult(status='error', error='GUI roles unavailable in the registered CLI environment')
        if self.agent.max_requests <= len(self.proxy.records) or self.agent.deadline - started < 5:
            return EpisodeResult(status='error', error='Shared physical request or absolute task budget exhausted')
        original_deadline = self.agent.deadline
        self.agent.deadline = min(original_deadline, started + budget.max_duration_seconds)
        role = 'executor' if self.role == 'cli_executor' else 'review' if self.role == 'cli_auditor' else self.role
        before = len(self.agent.rounds)
        try:
            # Every upstream role episode receives a fresh Hermes conversation;
            # only upstream-selected verified state/history enters its prompt.
            result = await self.agent.episode(prompt, self.proxy, role=role,
                                              container=self.agent.container, history=None)
            done = result.get('completed') is True
            actions = '\n'.join(json.dumps(m, ensure_ascii=False) for m in result.get('messages', []))
            if live_trajectory_path:
                target = env.local_path(live_trajectory_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(actions, encoding='utf-8')
            return EpisodeResult(status='done' if done else 'error', actions_log=actions,
                error=None if done else str(result.get('error') or result.get('failure_reason') or 'Hermes SDK did not complete'),
                duration_ms=int((time.monotonic() - started) * 1000),
                metadata={'assistant_visible_output': str(result.get('final_response') or ''),
                          'agent_done': done, 'exit_code': 0 if done else 1,
                          'backend': 'Hermes 0.21.3 / devin/swe-2', 'role': self.role})
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            return EpisodeResult(status='timeout', error=str(exc),
                                 duration_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            return EpisodeResult(status='error', error=type(exc).__name__ + ': ' + str(exc)[:1500],
                                 duration_ms=int((time.monotonic() - started) * 1000))
        finally:
            self.agent.deadline = original_deadline
            for record in self.agent.rounds[before:]:
                record.update(longhorizon_role=self.role, role_budget_seconds=budget.max_duration_seconds,
                              prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())


class LongHorizonStudy(SnapshotMixin, HermesSupergoalStudy):
    def __init__(self, *args, arm='longhorizon', **kwargs):
        if arm != 'longhorizon':
            raise ValueError('longhorizon condition required')
        super().__init__(*args, arm='native', **kwargs)

    def version(self):
        return 'longhorizon-0.1.7-hermes-dev02'

    async def run(self, instruction, environment, context):
        verify_upstream()
        from lh_harness.manager import run
        from lh_harness.types import HarnessConfig, EpisodeBudget
        self.deadline = time.monotonic() + self.budget_seconds
        env = ContainerEnvironment(self, environment)
        provider = json.loads((ROOT / 'private/model.json').read_text())
        report = {'arm': 'longhorizon', 'status': 'running', 'upstream_commit': PIN,
                  'control_id': self.uid, 'adapter': self.version(),
                  'adaptation': 'Unmodified upstream control loop; Hermes backend; terminal-only shared workspace tools for all roles',
                  'claude_backend_readonly_guard': False, 'max_requests': self.max_requests,
                  'max_manager_rounds': self.max_episodes, 'budget_seconds': self.budget_seconds}
        with ModelProxy(provider, limit=self.max_requests, journal=self.control / 'request-journal.json') as proxy:
            try:
                roles = {name: HermesRole(self, proxy, name) for name in [
                    'manager', 'cli_executor', 'gui_executor', 'cli_auditor', 'gui_auditor',
                    'auditor_format_repair', 'final_response']}
                config = HarnessConfig(max_total_episodes=self.max_episodes,
                    manager_budget=EpisodeBudget(min(300, int(self.budget_seconds))),
                    cli_executor_budget=EpisodeBudget(int(self.budget_seconds)),
                    gui_executor_budget=EpisodeBudget(int(self.budget_seconds)),
                    auditor_budget=EpisodeBudget(min(300, int(self.budget_seconds))),
                    workspace_path=self.cwd, harness_dir=env.remote_root,
                    log_dir=str(self.control / 'longhorizon'), prompt_language='en')
                result = await run(task=instruction, env=env, config=config, resume=False,
                    **{name + '_agent': backend for name, backend in roles.items()})
                # Upstream writes a cancellation receipt; still honor Harbor's
                # outer cancellation/deadline rather than swallowing its signal.
                if asyncio.current_task().cancelling():
                    raise asyncio.CancelledError
                report['upstream_result'] = result
                complete = result.get('completion_satisfied') is True
                report.update(status='succeeded' if complete else 'longhorizon_' + str(result.get('status')),
                              last_executor_claim=result.get('final_response', ''),
                              last_acceptance={'verdict': 'pass' if complete else 'unknown',
                                  'reason': 'Actual upstream completion gate', 'scope': 'Online harness decision, not official grade'})
            except BaseException as exc:
                report.update(status='adapter_error', error_type=type(exc).__name__, error=str(exc)[:1500])
                raise
            finally:
                try:
                    report['artifact_image_id'] = await self.docker('commit', self.container, 'sg-artifact:' + self.uid)
                except Exception as exc:
                    report['artifact_snapshot_error'] = type(exc).__name__ + ': ' + str(exc)[:900]
                report.update(rounds=self.rounds, requests=proxy.records, used_requests=len(proxy.records),
                              snapshot_lifecycle=self.snapshot_receipts, proxy_rejections=proxy.rejections)
                if any(r.get('state_restored') is False for r in self.snapshot_receipts):
                    report.update(status='adapter_error', error_type='SnapshotStateError')
                dump(self.control / 'report.json', report)
                context.metadata = {'study_arm': 'longhorizon', 'control_id': self.uid,
                                    'study_status': report['status'], 'adapter': self.version(),
                                    'physical_requests': len(proxy.records), 'upstream_commit': PIN}
                usages = [r.get('usage') or {} for r in proxy.records]
                context.n_input_tokens = sum(u.get('input_tokens', 0) or 0 for u in usages)
                context.n_output_tokens = sum(u.get('output_tokens', 0) or 0 for u in usages)
                context.n_cache_tokens = sum((u.get('input_tokens_details') or {}).get('cached_tokens', 0) or 0 for u in usages)
