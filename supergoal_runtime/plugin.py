"""Tiny /sgx consumer for the Hermes host ABI spike."""

from __future__ import annotations

from typing import Any


_ACTIVE_SESSIONS: set[str] = set()


async def _handle_sgx(ctx: Any, raw_args: str) -> str:
    args = (raw_args or "").strip().split()
    if not args or args[0] != "start":
        return "Usage: /sgx start"
    _ACTIVE_SESSIONS.add(ctx.session_id)
    return f"sgx started for session {ctx.session_id}"


def _after_turn(ctx: Any):
    if ctx.session_id not in _ACTIVE_SESSIONS:
        return None
    _ACTIVE_SESSIONS.remove(ctx.session_id)
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


def _on_session_rotate(
    *,
    old_session_id: str,
    new_session_id: str,
    reason: str,
    **_: Any,
) -> None:
    if reason == "compression" and old_session_id in _ACTIVE_SESSIONS:
        _ACTIVE_SESSIONS.remove(old_session_id)
        _ACTIVE_SESSIONS.add(new_session_id)


def register(ctx: Any) -> None:
    ctx.register_command(
        "sgx",
        _handle_sgx,
        description="Start the temporary Supergoal ABI spike",
        args_hint="start",
        context_aware=True,
    )
    ctx.register_turn_controller(
        "supergoal-runtime-phase1",
        _after_turn,
        priority=100,
    )
    ctx.register_hook("on_session_rotate", _on_session_rotate)
