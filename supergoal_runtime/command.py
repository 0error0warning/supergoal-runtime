"""Context-aware /sgx command handler."""

from __future__ import annotations

from typing import Any

from .compat.hermes_goal import ordinary_goal_active
from .prompts import START_PROMPT_PREFIX
from .runtime import RuntimeManager


class SupergoalCommandHandler:
    def __init__(self, runtime: RuntimeManager) -> None:
        self.runtime = runtime

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
            state = self.runtime.start(session_id, rest)
            prompt = f"{START_PROMPT_PREFIX}\nGoal: {state.goal}"
            enqueued = await ctx.enqueue_followup(prompt)
            return f"Started /sgx supergoal {state.goal_run_id}." if enqueued else "Started /sgx supergoal, but the host did not enqueue a follow-up turn."

        if verb == "status" or verb == "":
            if raw and verb not in {"status"}:
                return "Usage: /sgx start <mission>. Plain /sgx <text> does not start a supergoal."
            return self.runtime.status_text(session_id)
        if verb == "pause":
            return self.runtime.pause(session_id)
        if verb == "resume":
            text, prompt = self.runtime.resume(session_id)
            if prompt:
                await ctx.enqueue_followup(prompt)
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
            text, prompt = self.runtime.replan(session_id)
            if prompt:
                await ctx.enqueue_followup(prompt)
            return text
        return "Usage: /sgx start <mission>. Plain /sgx <text> does not start a supergoal."


def register_supergoal_command(ctx: Any, handler: SupergoalCommandHandler, *, name: str = "sgx") -> None:
    ctx.register_command(
        name,
        handler,
        description="Run a long-lived Supergoal mission",
        args_hint="start <mission>|status|pause|resume|clear|wait|unwait|replan",
        context_aware=True,
        busy_safe_subcommands=("", "status", "pause", "resume", "clear", "wait", "unwait", "replan"),
    )
