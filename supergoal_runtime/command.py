"""Context-aware /sgx command handler."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from .compat.hermes_goal import ordinary_goal_active
from .runtime import RuntimeManager


_CONTEXT_AWARE_MISSING = (
    "/sgx requires a context-aware host CommandContext (session_id + "
    "enqueue_followup). This Hermes build only invokes plugin commands as "
    "fn(raw_args) and has no register_turn_controller ABI. Slash /sgx is "
    "unavailable until the mt context-aware plugin command ABI is restored."
)


class SupergoalCommandHandler:
    def __init__(self, runtime: RuntimeManager, *, continuation_provider: Any = None) -> None:
        self.runtime = runtime
        self.continuation_provider = continuation_provider

    async def _enqueue_followup(self, ctx: Any, envelope: dict[str, Any]) -> bool:
        kwargs = {
            "plugin_id": "supergoal-runtime",
            "controller_name": "supergoal-runtime",
            "continuation_token": str(envelope["token"]),
            "state_version": int(envelope["state_version"]),
        }
        try:
            return bool(await ctx.enqueue_followup(str(envelope["prompt"]), **kwargs))
        except TypeError:
            # Source-compatible fallback for older hosts and lightweight test
            # contexts. Claim locally because the legacy host cannot do it.
            provider = self.continuation_provider
            claim = getattr(provider, "claim_continuation", None)
            if callable(claim) and not claim(
                session_id=str(envelope["session_id"]),
                token=str(envelope["token"]),
                state_version=int(envelope["state_version"]),
            ):
                return False
            return bool(await ctx.enqueue_followup(str(envelope["prompt"])))

    async def __call__(self, ctx: Any, raw_args: str) -> str:
        raw = (raw_args or "").strip()
        parts = raw.split(maxsplit=1)
        verb = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        session_id = str(getattr(ctx, "session_id", "") or "")

        if verb == "start":
            if not rest:
                return "Usage: /sgx start <mission>"
            if ordinary_goal_active(session_id):
                return "Cannot start /sgx while ordinary /goal is active in this session. Clear or pause /goal first."
            if self.runtime.load_state_for_session(session_id) is not None:
                return "A /sgx supergoal is already bound to this session."
            state, envelope = self.runtime.start_for_command(session_id, rest)
            enqueued = await self._enqueue_followup(ctx, envelope)
            return f"Started /sgx supergoal {state.goal_run_id}." if enqueued else "Started /sgx supergoal, but the host did not enqueue a follow-up turn."

        if verb == "status" or verb == "":
            if raw and verb not in {"status"}:
                return "Usage: /sgx start <mission>. Plain /sgx <text> does not start a supergoal."
            return self.runtime.status_text(session_id)
        if verb == "pause":
            return self.runtime.pause(session_id)
        if verb == "resume":
            text, envelope = self.runtime.resume_for_command(session_id)
            if envelope:
                await self._enqueue_followup(ctx, envelope)
            return text
        if verb == "clear":
            return self.runtime.clear(session_id)
        if verb == "wait":
            if not rest:
                return "Usage: /sgx wait <pid>"
            try:
                return self.runtime.wait(session_id, rest)
            except ValueError as exc:
                return f"/sgx wait: {exc}"
        if verb == "unwait":
            return self.runtime.unwait(session_id)
        if verb == "replan":
            text, envelope = self.runtime.replan_for_command(session_id)
            if envelope:
                await self._enqueue_followup(ctx, envelope)
            return text
        return "Usage: /sgx start <mission>. Plain /sgx <text> does not start a supergoal."


def _adapt_context_aware_handler(handler: Callable[..., Any]) -> Callable[[str], Any]:
    async def adapted(raw_args: str) -> str:
        return _CONTEXT_AWARE_MISSING

    adapted.__name__ = getattr(handler, "__name__", "adapted_sgx")
    adapted.__doc__ = getattr(handler, "__doc__", None)
    return adapted


def register_supergoal_command(ctx: Any, handler: SupergoalCommandHandler, *, name: str = "sgx") -> None:
    kwargs = dict(
        description="Run a long-lived Supergoal mission",
        args_hint=getattr(handler, "args_hint", "start <mission>|status|pause|resume|clear|wait|unwait|replan"),
        context_aware=True,
        busy_safe_subcommands=getattr(handler, "busy_safe_subcommands", ("", "status", "pause", "resume", "clear", "wait", "unwait", "replan")),
    )
    sig = inspect.signature(ctx.register_command)
    accepts_extra = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    filtered = {key: value for key, value in kwargs.items() if accepts_extra or key in sig.parameters}
    command_handler: Callable[..., Any] = handler
    if kwargs.get("context_aware") and not accepts_extra and "context_aware" not in sig.parameters:
        command_handler = _adapt_context_aware_handler(handler)
    ctx.register_command(name, command_handler, **filtered)
