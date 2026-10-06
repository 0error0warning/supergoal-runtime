"""Hermes registration for the standalone Supergoal runtime."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

from .command import SupergoalCommandHandler, register_supergoal_command
from .policy import ToolHookHandler
from .runtime import RuntimeManager
from .store import BindingConflictError, SupergoalStore


LLM_EVALUATION_TIMEOUT_SECONDS = 18.0
CONTROLLER_STORE_TIMEOUT_SECONDS = 2.0
COMPRESSION_MISS_CACHE_MAX = 1024
COMPRESSION_MISS_CACHE_TTL_SECONDS = 300.0

logger = logging.getLogger(__name__)


def _get_compression_lineage(session_id: str) -> list[str]:
    """Read Hermes' fork-aware compression lineage without opening a writer."""

    from hermes_state import SessionDB

    with SessionDB(read_only=True) as session_db:
        get_lineage = getattr(session_db, "get_compression_lineage", None)
        if not callable(get_lineage):
            raise RuntimeError(
                "Hermes SessionDB does not expose get_compression_lineage()"
            )
        raw_lineage = get_lineage(session_id)
    return [str(item) for item in (raw_lineage or ()) if str(item)]


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
        self._compression_misses: OrderedDict[str, float] = OrderedDict()
        self._compression_misses_lock = threading.Lock()
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

    def _compression_miss_is_cached(self, session_id: str) -> bool:
        now = time.monotonic()
        with self._compression_misses_lock:
            expires_at = self._compression_misses.get(session_id)
            if expires_at is None:
                return False
            if expires_at <= now:
                self._compression_misses.pop(session_id, None)
                return False
            self._compression_misses.move_to_end(session_id)
            return True

    def _remember_compression_miss(self, session_id: str) -> None:
        with self._compression_misses_lock:
            self._compression_misses.pop(session_id, None)
            self._compression_misses[session_id] = (
                time.monotonic() + COMPRESSION_MISS_CACHE_TTL_SECONDS
            )
            while len(self._compression_misses) > COMPRESSION_MISS_CACHE_MAX:
                self._compression_misses.popitem(last=False)

    def _forget_compression_miss(self, session_id: str) -> None:
        with self._compression_misses_lock:
            self._compression_misses.pop(session_id, None)

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

    def post_llm_call(
        self,
        *,
        session_id: str | None = None,
        **_: Any,
    ) -> None:
        """Reconcile a compression child with its active logical mission.

        Hermes invokes this supported hook after compression and before the
        post-turn controller.  The host's compression lineage deliberately
        excludes explicit branches, delegates, and tool sessions, so only a
        genuine compression continuation can inherit a Supergoal binding.
        """

        current_session_id = str(session_id or "").strip()
        if not current_session_id or not self.store.db_path.exists():
            return
        if self._compression_miss_is_cached(current_session_id):
            return
        try:
            if self.store.get_goal_run_id(current_session_id):
                self._forget_compression_miss(current_session_id)
                return

            lineage = _get_compression_lineage(current_session_id)
            try:
                current_index = lineage.index(current_session_id)
            except ValueError:
                self._remember_compression_miss(current_session_id)
                return
            lineage = lineage[: current_index + 1]
            old_session_id = next(
                (
                    ancestor
                    for ancestor in reversed(lineage[:-1])
                    if self.store.is_current_session(ancestor)
                ),
                "",
            )
            if not old_session_id:
                self._remember_compression_miss(current_session_id)
                return

            goal_run_id = self.store.rotate_session_binding(
                old_session_id,
                current_session_id,
                reason="compression",
            )
            if not goal_run_id:
                if self.store.get_goal_run_id(current_session_id):
                    self._forget_compression_miss(current_session_id)
                else:
                    self._remember_compression_miss(current_session_id)
                return
            self._forget_compression_miss(current_session_id)
        except BindingConflictError:
            logger.warning(
                "Supergoal compression binding conflict for session %s",
                current_session_id,
                exc_info=True,
            )
            return
        except Exception:
            logger.warning(
                "Supergoal compression reconciliation failed open for session %s",
                current_session_id,
                exc_info=True,
            )
            return

        try:
            self.store.append_event_once(
                goal_run_id,
                {
                    "ts": time.time(),
                    "type": "session_rotated",
                    "turn": int(
                        (self.store.load_run(goal_run_id) or {}).get(
                            "turns_used", 0
                        )
                        or 0
                    ),
                    "summary": "Hermes compression lineage reconciled",
                    "data": {
                        "old_session_id": old_session_id,
                        "new_session_id": current_session_id,
                        "reason": "compression",
                    },
                },
                source_key=(
                    "compression-lineage:"
                    f"{old_session_id}:{current_session_id}"
                ),
            )
        except Exception:
            # Continuity is already durable.  An audit-row failure must not
            # break an otherwise successful Hermes response.
            logger.warning(
                "Supergoal compression audit event failed for session %s",
                current_session_id,
                exc_info=True,
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
    get_config = getattr(ctx, "get_config", None)
    engine = get_config("engine", "v1") if callable(get_config) else "v1"
    if engine == "v2":
        from .v2.plugin import register as register_v2
        register_v2(ctx)
        return
    if engine != "v1":
        raise ValueError(f"Unsupported Supergoal engine: {engine}")
    runtime = _PluginRuntime(ctx)
    for command_name in ("sgx", "supergoal", "sgoal"):
        register_supergoal_command(ctx, runtime.commands, name=command_name)
    register_turn_controller = getattr(ctx, "register_turn_controller", None)
    if callable(register_turn_controller):
        try:
            register_turn_controller(
                "supergoal-runtime",
                runtime.after_turn,
                priority=100,
                continuation_provider=runtime,
            )
        except TypeError:
            # The minimum supported legacy host has no provider keyword. Command
            # enqueueing still claims locally; upgrading the host enables recovery.
            register_turn_controller(
                "supergoal-runtime",
                runtime.after_turn,
                priority=100,
            )
    else:
        # Hermes >=0.21.3 dropped the turn-controller ABI. Hooks below still
        # provide compression/session lifecycle continuity without after_turn.
        logger.warning(
            "supergoal-runtime: host has no register_turn_controller; "
            "after_turn controller not registered (hooks only)"
        )
    ctx.register_hook("on_session_finalize", runtime.on_session_finalize)
    ctx.register_hook("on_session_reset", runtime.on_session_reset)
    ctx.register_hook("post_llm_call", runtime.post_llm_call)
    ctx.register_hook("pre_tool_call", runtime.tool_hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", runtime.tool_hooks.post_tool_call)
