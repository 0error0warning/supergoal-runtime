"""Opt-in v2 adapter for the real Hermes command/turn-controller ABI."""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from ..command import register_supergoal_command
from ..compat.hermes_goal import ordinary_goal_active
from ..config import get_hermes_home
from .acceptance import calibrate, load_policy, verify_policy
from .kernel import StaleLease
from .workflow import Workflow, token_for


LOG = logging.getLogger(__name__)
DISPUTE = re.compile(r"<supergoal-dispute>([\s\S]{1,8000}?)</supergoal-dispute>")


class V2Runtime:
    args_hint = "start <policy.json>|status|pause|resume|clear|wait <event>|wake <event>|dispute <reason>|amend <policy.json>|reconcile <evidence>"
    busy_safe_subcommands = ("", "status", "pause", "clear", "dispute")

    def __init__(self, ctx: Any):
        self.owner = "v2_" + uuid.uuid4().hex
        self.store = Workflow(get_hermes_home() / "supergoal" / "v2.sqlite3")
        registrar = getattr(ctx, "register_turn_controller", None)
        params = inspect.signature(registrar).parameters if callable(registrar) else {}
        self.compatible = callable(registrar) and ("continuation_provider" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()))

    def claim_continuation(self, *, session_id, token, state_version=None):
        return self.store.claim_envelope(session_id, token, self.owner, state_version)

    def recover_continuations(self):
        return self.store.recover(self.owner)

    def status(self, session_id):
        goal = self.store.bound(session_id)
        if not goal:
            return "No Supergoal v2 goal in this session. Start with /supergoal start <absolute-policy.json>."
        state = self.store.state(goal)
        policy = state["contract"]["acceptance"]
        reason = (state["last_outcome"] or {}).get("reason", "")
        return (f"Supergoal v2 {goal}: {state['status']}; turns {state['turns']}/{state['max_turns']}.\n"
                f"Goal: {state['contract']['outcome']}\nAcceptance: {policy['fingerprint'][:16]}\n"
                f"Verified scope: {policy['scope']}\n{reason}").rstrip()

    async def enqueue(self, ctx):
        envelope = self.store.envelope(str(ctx.session_id))
        if not envelope:
            return False
        # No legacy fallback: v2 requires a host-side claim immediately before dispatch.
        return bool(await ctx.enqueue_followup(envelope["prompt"], plugin_id="supergoal-runtime",
                    controller_name="supergoal-runtime", continuation_token=envelope["token"],
                    state_version=envelope["state_version"]))

    async def __call__(self, ctx, raw_args: str):
        sid = str(getattr(ctx, "session_id", "") or "")
        parts = (raw_args or "").strip().split(maxsplit=1)
        verb, rest = (parts[0].lower(), parts[1].strip() if len(parts) > 1 else "") if parts else ("status", "")
        if not sid:
            return "Supergoal v2 requires the host's context-aware session identity."
        if verb in {"", "status"}:
            return self.status(sid)
        if not self.compatible:
            return "Supergoal v2 requires the durable continuation-provider ABI; this host cannot dispatch v2 work."
        try:
            if verb == "start":
                if not rest:
                    return "Usage: /supergoal start <absolute-policy.json>"
                if ordinary_goal_active(sid):
                    return "Pause or clear the ordinary Hermes /goal first."
                from ..store import SupergoalStore
                legacy_store = SupergoalStore()
                if legacy_store.db_path.exists() and legacy_store.get_goal_run_id(sid):
                    return "This session has a v1 binding. Clear it using engine=v1 or start v2 in a fresh session."
                policy = load_policy(rest.strip('"'))
                calibration = await asyncio.to_thread(calibrate, policy)
                if not calibration["valid"]:
                    observed = [(c["expected"], c["observed"]["verdict"]) for c in calibration.get("cases", [])]
                    return f"Acceptance calibration failed; no goal was started. Expected/observed: {observed}"
                self.store.start(sid, policy, calibration)
                queued = await self.enqueue(ctx)
                return self.status(sid) + ("\nFirst turn queued." if queued else "\nWork is durable but not queued; resume or host recovery can enqueue it.")
            goal = self.store.bound(sid)
            if not goal:
                return self.status(sid)
            if verb == "pause":
                self.store.hold(goal, reason="operator paused the goal")
            elif verb == "resume":
                self.store.resume(goal)
                await self.enqueue(ctx)
            elif verb == "clear":
                self.store.clear(sid)
                return "Cleared the session binding; the v2 audit history is retained."
            elif verb == "dispute":
                self.store.hold(goal, status="contract_disputed", reason=rest)
            elif verb == "amend":
                policy = load_policy(rest.strip('"'))
                calibration = await asyncio.to_thread(calibrate, policy)
                self.store.amend(goal, policy, calibration)
                return self.status(sid) + "\nAmendment recorded. Use resume after reviewing any uncertain execution."
            elif verb == "reconcile":
                self.store.reconcile(goal, note=rest)
                return self.status(sid) + "\nExecution review recorded. Use resume to continue."
            elif verb == "wait":
                self.store.wait_for(goal, rest)
            elif verb == "wake":
                if not self.store.signal(goal, rest):
                    return "No matching pending input event; nothing was dispatched."
                await self.enqueue(ctx)
            else:
                return "Usage: /supergoal " + self.args_hint
            return self.status(sid)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return f"Supergoal v2: {exc}"

    def _directive(self, sid, action, *, envelope=None):
        from hermes_cli.plugins import TurnDirective
        goal = self.store.bound(sid)
        state = self.store.state(goal)
        fields = dict(action=action, notice=self.status(sid), state_version=state["version"],
                      dedupe_key=f"{goal}:{state['version']}:{action}")
        if envelope:
            fields.update(continuation_prompt=envelope["prompt"], continuation_token=envelope["token"])
        return TurnDirective(**fields)

    def after_turn(self, ctx):
        sid = str(getattr(ctx, "session_id", "") or "")
        lease = self.store.running(sid, self.owner)
        if not lease:
            return None
        state = self.store.state(lease.goal_id)
        expected = f"[Supergoal v2 {token_for(lease.goal_id, lease.item, state['version'])}]"
        if getattr(ctx, "interrupted", False) or not str(getattr(ctx, "user_message", "")).startswith(expected + "\n"):
            self.store.hold(lease.goal_id, reason="execution interrupted or user preempted the continuation", actor="host")
            return self._directive(sid, "pause")
        final = str(getattr(ctx, "final_response", "") or "")
        dispute = DISPUTE.search(final)
        if getattr(ctx, "background_processes", None):
            # A background writer would invalidate any artifact snapshot after checking.
            self.store.hold(lease.goal_id, reason="background processes require a settled checkpoint before acceptance", actor="host")
            return self._directive(sid, "pause")
        contract = state["contract"]
        outcome = verify_policy(Path(contract["workspace"]), contract["acceptance"], contract["calibration"])
        if dispute:
            outcome = {**outcome, "verdict": "disputed", "measured_verdict": outcome["verdict"],
                       "reason": "executor requested acceptance review", "executor_evidence": dispute.group(1)}
        try:
            result = self.store.finish(lease, outcome)
        except StaleLease:
            # Operator pause/cancel during verification wins; no continuation is created.
            return None
        if result["status"] == "active":
            return self._directive(sid, "continue", envelope=self.store.envelope(sid))
        return self._directive(sid, "done" if result["status"] == "succeeded" else "pause")

    def post_llm_call(self, *, session_id=None, **_):
        sid = str(session_id or "")
        if not sid or self.store.bound(sid):
            return
        try:
            from ..plugin import _get_compression_lineage
            lineage = _get_compression_lineage(sid)
            if sid not in lineage:
                return
            for ancestor in reversed(lineage[:lineage.index(sid)]):
                if self.store.bound(ancestor):
                    self.store.rotate(ancestor, sid)
                    return
        except Exception:
            LOG.warning("v2 compression binding was not reconciled", exc_info=True)

    def on_session_finalize(self, *, session_id=None, reason="session_finalize", **_):
        if reason == "shutdown":
            return
        if goal := self.store.bound(str(session_id or "")):
            self.store.hold(goal, reason=str(reason), actor="host")

    def on_session_reset(self, *, old_session_id=None, reason="session_reset", **_):
        self.on_session_finalize(session_id=old_session_id, reason=reason)


def register(ctx):
    runtime = V2Runtime(ctx)
    for name in ("sgx", "supergoal", "sgoal"):
        register_supergoal_command(ctx, runtime, name=name)
    if runtime.compatible:
        ctx.register_turn_controller("supergoal-runtime", runtime.after_turn, priority=100,
                                     continuation_provider=runtime)
    ctx.register_hook("post_llm_call", runtime.post_llm_call)
    ctx.register_hook("on_session_finalize", runtime.on_session_finalize)
    ctx.register_hook("on_session_reset", runtime.on_session_reset)
