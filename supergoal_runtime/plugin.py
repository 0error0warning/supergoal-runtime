"""Hermes registration and temporary Phase 1 command bridge.

The production Supergoal command/controller moves here in later phases. Phase 2
replaces process-global spike state with the plugin-owned SQLite repository.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .store import BindingConflictError, SupergoalStore


class _PluginRuntime:
    def __init__(self) -> None:
        self._store_instance: SupergoalStore | None = None

    @property
    def store(self) -> SupergoalStore:
        if self._store_instance is None:
            self._store_instance = SupergoalStore()
        return self._store_instance

    async def handle_sgx(self, ctx: Any, raw_args: str) -> str:
        args = (raw_args or "").strip().split()
        if not args or args[0] != "start":
            return "Usage: /sgx start"

        existing = self.store.get_goal_run_id(ctx.session_id)
        if existing:
            return f"sgx already active for session {ctx.session_id}"

        goal_run_id = f"gr_{uuid.uuid4().hex[:16]}"
        state = {
            "goal": "Phase 1 host ABI continuation spike",
            "goal_run_id": goal_run_id,
            "mode": "supergoal",
            "status": "active",
            "turns_used": 0,
            "phase1_continuation_pending": True,
            "created_at": time.time(),
        }
        self.store.import_run_bundle(
            goal_run_id,
            state,
            bindings=[(ctx.session_id, "sgx_start")],
            events=[
                (
                    {
                        "ts": time.time(),
                        "type": "set",
                        "turn": 0,
                        "summary": "temporary /sgx run started",
                        "data": {"session_id": ctx.session_id},
                    },
                    "phase2:sgx_start",
                    0,
                )
            ],
        )
        return f"sgx started for session {ctx.session_id}"

    def after_turn(self, ctx: Any):
        goal_run_id = self.store.get_goal_run_id(ctx.session_id)
        if not goal_run_id or not self.store.is_current_session(ctx.session_id):
            return None
        state = self.store.load_run(goal_run_id)
        if not state or state.get("status") != "active":
            return None
        if not state.get("phase1_continuation_pending", False):
            return None

        state["phase1_continuation_pending"] = False
        state["turns_used"] = int(state.get("turns_used", 0) or 0) + 1
        self.store.save_run_with_events(
            goal_run_id,
            state,
            [
                {
                    "ts": time.time(),
                    "type": "continuation_enqueued",
                    "turn": state["turns_used"],
                    "summary": "Phase 1 continuation returned to host",
                    "data": {"session_id": ctx.session_id},
                }
            ],
        )
        try:
            from hermes_cli.plugins import TurnDirective
        except Exception:
            return {
                "action": "continue",
                "continuation_prompt": "Continue the /sgx Phase 1 spike.",
                "dedupe_key": f"sgx:{ctx.session_id}:1",
                "state_version": 1,
            }
        return TurnDirective(
            action="continue",
            continuation_prompt="Continue the /sgx Phase 1 spike.",
            dedupe_key=f"sgx:{ctx.session_id}:1",
            state_version=1,
        )

    def on_session_rotate(
        self,
        *,
        old_session_id: str,
        new_session_id: str,
        reason: str,
        **_: Any,
    ) -> None:
        if reason != "compression":
            return
        try:
            goal_run_id = self.store.rotate_session_binding(
                old_session_id,
                new_session_id,
                reason="compression",
            )
        except BindingConflictError:
            return
        if not goal_run_id:
            return
        self.store.append_event(
            goal_run_id,
            {
                "ts": time.time(),
                "type": "session_rotated",
                "turn": int(
                    (self.store.load_run(goal_run_id) or {}).get("turns_used", 0) or 0
                ),
                "summary": "Hermes session rotated after compression",
                "data": {
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id,
                    "reason": reason,
                },
            },
        )


def register(ctx: Any) -> None:
    runtime = _PluginRuntime()
    ctx.register_command(
        "sgx",
        runtime.handle_sgx,
        description="Start the temporary Supergoal ABI spike",
        args_hint="start",
        context_aware=True,
    )
    ctx.register_turn_controller(
        "supergoal-runtime-phase2",
        runtime.after_turn,
        priority=100,
    )
    ctx.register_hook("on_session_rotate", runtime.on_session_rotate)
