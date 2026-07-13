"""Hermes registration for the standalone Supergoal runtime."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .command import SupergoalCommandHandler, register_supergoal_command
from .policy import ToolHookHandler
from .runtime import RuntimeManager
from .store import BindingConflictError, SupergoalStore


LLM_EVALUATION_TIMEOUT_SECONDS = 18.0
CONTROLLER_STORE_TIMEOUT_SECONDS = 2.0


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1 :]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None


def _llm_text(llm: Any, messages: list[dict[str, str]]) -> str:
    if llm is None:
        return ""
    if hasattr(llm, "complete"):
        callback = llm.complete
    elif hasattr(llm, "chat"):
        callback = llm.chat
    elif callable(llm):
        callback = llm
    else:
        return ""
    try:
        result = callback(
            messages=messages,
            timeout=LLM_EVALUATION_TIMEOUT_SECONDS,
        )
    except TypeError as exc:
        # Lightweight fakes and the oldest supported host callback do not
        # necessarily expose ``timeout``. Retry only when that keyword is the
        # incompatibility; a TypeError raised by the callback itself remains a
        # real evaluator failure.
        if "timeout" not in str(exc):
            raise
        result = callback(messages=messages)
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return str(result.get("content") or result.get("text") or "")
    return str(getattr(result, "content", "") or getattr(result, "text", "") or "")


def _judge_from_ctx(ctx: Any):
    from .prompts import build_judge_messages

    def judge(goal: str, last_response: str, **kwargs: Any) -> tuple[str, str, bool]:
        state = kwargs.get("state")
        render_board = getattr(state, "render_supergoal_board", None)
        state_board = str(render_board() or "") if callable(render_board) else ""
        messages = build_judge_messages(
            goal,
            last_response,
            subgoals=kwargs.get("subgoals"),
            state_board=state_board,
            turn_number=int(getattr(state, "turns_used", 0) or 0),
        )
        raw = _llm_text(getattr(ctx, "llm", None), messages)
        data = _parse_json_object(raw)
        if not data:
            return "continue", "judge callback unavailable or returned non-JSON", False
        done = data.get("done")
        if isinstance(done, str):
            done_bool = done.strip().lower() in {"true", "yes", "1", "done"}
        else:
            done_bool = bool(done)
        return ("done" if done_bool else "continue", str(data.get("reason") or "no reason provided"), False)

    return judge


def _critic_from_ctx(ctx: Any):
    from .prompts import build_critic_messages

    def critic(state: Any, last_response: str) -> dict[str, Any] | None:
        raw = _llm_text(getattr(ctx, "llm", None), build_critic_messages(state, last_response))
        return _parse_json_object(raw)

    return critic


class _PluginRuntime:
    def __init__(self, ctx: Any) -> None:
        self.boot_id = f"boot_{uuid.uuid4().hex}"
        # Core bounds a turn controller at 30 seconds. The evaluators share a
        # 20-second budget, so database lock waits must stay short enough for
        # the final CAS to complete (or fail closed) before the host deadline.
        self.store = SupergoalStore(timeout=CONTROLLER_STORE_TIMEOUT_SECONDS)
        self.manager = RuntimeManager(
            store=self.store,
            judge=_judge_from_ctx(ctx),
            critic=_critic_from_ctx(ctx),
        )
        self.commands = SupergoalCommandHandler(
            self.manager,
            continuation_provider=self,
        )
        self.tool_hooks = ToolHookHandler(self.store)

    def claim_continuation(
        self,
        *,
        session_id: str,
        token: str,
        state_version: int | None = None,
    ) -> bool:
        return self.store.claim_continuation(
            session_id=session_id,
            token=token,
            state_version=state_version,
            claim_owner=self.boot_id,
        )

    def recover_continuations(self) -> list[dict[str, Any]]:
        return self.store.recover_continuations(claim_owner=self.boot_id)

    def after_turn(self, ctx: Any) -> Any:
        directive = self.manager.after_turn(
            str(getattr(ctx, "session_id", "") or ""),
            final_response=str(getattr(ctx, "final_response", "") or ""),
            turn_id=str(getattr(ctx, "turn_id", "") or ""),
            user_message=str(getattr(ctx, "user_message", "") or ""),
            interrupted=bool(getattr(ctx, "interrupted", False)),
            background_processes=list(getattr(ctx, "background_processes", []) or []),
        )
        if not directive:
            return None
        try:
            from hermes_cli.plugins import TurnDirective
        except Exception:
            return directive
        try:
            return TurnDirective(**directive)
        except TypeError:
            # Older hosts know the turn-controller ABI but not durable
            # continuation metadata. Preserve their existing behavior while
            # claiming the plugin-owned row locally so this process cannot
            # recover the same turn twice.
            legacy = dict(directive)
            token = legacy.pop("continuation_token", None)
            if token is not None and not self.claim_continuation(
                session_id=str(getattr(ctx, "session_id", "") or ""),
                token=str(token),
                state_version=legacy.get("state_version"),
            ):
                return None
            return TurnDirective(**legacy)

    def on_session_rotate(
        self,
        *,
        old_session_id: str,
        new_session_id: str,
        reason: str,
        **_: Any,
    ) -> None:
        if reason not in {"compression", "context_compression"}:
            return
        try:
            goal_run_id = self.store.rotate_session_binding(
                old_session_id,
                new_session_id,
                reason=reason,
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
                "turn": int((self.store.load_run(goal_run_id) or {}).get("turns_used", 0) or 0),
                "summary": "Hermes session rotated",
                "data": {
                    "old_session_id": old_session_id,
                    "new_session_id": new_session_id,
                    "reason": reason,
                },
            },
        )

    def on_session_finalize(
        self,
        *,
        session_id: str | None = None,
        reason: str = "session_finalize",
        **_: Any,
    ) -> None:
        # Shutdown is a process boundary, not a conversation boundary. Leave
        # active state and open outbox rows recoverable by the next boot owner.
        if str(reason or "").strip().lower() == "shutdown":
            return
        self.store.finalize_session(
            str(session_id or ""),
            reason=str(reason or "session_finalize"),
        )

    def on_session_reset(
        self,
        *,
        old_session_id: str | None = None,
        reason: str = "session_reset",
        **_: Any,
    ) -> None:
        self.store.finalize_session(
            str(old_session_id or ""),
            reason=str(reason or "session_reset"),
        )


def register(ctx: Any) -> None:
    runtime = _PluginRuntime(ctx)
    for command_name in ("sgx", "supergoal", "sgoal"):
        register_supergoal_command(ctx, runtime.commands, name=command_name)
    try:
        ctx.register_turn_controller(
            "supergoal-runtime",
            runtime.after_turn,
            priority=100,
            continuation_provider=runtime,
        )
    except TypeError:
        # The minimum supported legacy host has no provider keyword. Command
        # enqueueing still claims locally; upgrading the host enables recovery.
        ctx.register_turn_controller(
            "supergoal-runtime",
            runtime.after_turn,
            priority=100,
        )
    ctx.register_hook("on_session_finalize", runtime.on_session_finalize)
    ctx.register_hook("on_session_reset", runtime.on_session_reset)
    ctx.register_hook("on_session_rotate", runtime.on_session_rotate)
    ctx.register_hook("pre_tool_call", runtime.tool_hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", runtime.tool_hooks.post_tool_call)
