"""Supergoal policy and evidence hooks for Hermes tool calls."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from .domain import GoalState
from .evidence import evidence_ref_from_tool_call, redact_sensitive
from .store import SupergoalStore

PolicyDecision = Literal["allow", "deny", "require_user_approval", "sandbox_only"]


@dataclass
class PermissionContract:
    filesystem_allowlist: list[str] = field(default_factory=list)
    network_allowlist: list[str] = field(default_factory=list)
    api_scopes: list[str] = field(default_factory=list)
    destructive_actions: Literal["deny", "ask", "allow"] = "ask"
    trading_mode: Literal["dry_run", "paper", "live_forbidden", "live_allowed"] = "live_forbidden"
    max_cost_usd: float | None = None
    max_runtime_minutes: int | None = None
    requires_human_for: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Any) -> "PermissionContract":
        if not isinstance(data, dict):
            return cls()
        destructive = str(data.get("destructive_actions") or "ask").lower()
        if destructive not in {"deny", "ask", "allow"}:
            destructive = "ask"
        trading = str(data.get("trading_mode") or "live_forbidden").lower()
        if trading not in {"dry_run", "paper", "live_forbidden", "live_allowed"}:
            trading = "live_forbidden"
        return cls(
            filesystem_allowlist=_list(data.get("filesystem_allowlist")),
            network_allowlist=_list(data.get("network_allowlist")),
            api_scopes=_list(data.get("api_scopes")),
            destructive_actions=destructive,  # type: ignore[arg-type]
            trading_mode=trading,  # type: ignore[arg-type]
            max_cost_usd=_float_or_none(data.get("max_cost_usd")),
            max_runtime_minutes=_int_or_none(data.get("max_runtime_minutes")),
            requires_human_for=_list(data.get("requires_human_for")),
        )


@dataclass(frozen=True)
class PolicyGuardResult:
    decision: PolicyDecision = "allow"
    reason: str = ""

    @property
    def allows_execution(self) -> bool:
        return self.decision == "allow"

    def block_message(self) -> str:
        return json.dumps({"error": f"Supergoal policy {self.decision}: {self.reason}"}, ensure_ascii=False)


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()][:32]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _path_allowed(path: str, allowlist: list[str], *, base_dir: str = "") -> bool:
    if not allowlist or not path:
        return True
    try:
        expanded = Path(os.path.expanduser(path))
        if not expanded.is_absolute() and base_dir:
            expanded = Path(os.path.expanduser(base_dir)) / expanded
        p = expanded.resolve()
    except Exception:
        return False
    for allowed in allowlist:
        try:
            a = Path(os.path.expanduser(allowed)).resolve()
            if p == a or a in p.parents:
                return True
        except Exception:
            continue
    return False


def _host_allowed(url_or_host: str, allowlist: list[str]) -> bool:
    if not allowlist or not url_or_host:
        return True
    parsed = urlparse(url_or_host if "://" in url_or_host else f"https://{url_or_host}")
    host = (parsed.hostname or url_or_host).lower()
    for allowed in allowlist:
        allowed_host = (urlparse(allowed if "://" in allowed else f"https://{allowed}").hostname or allowed).lower()
        if host == allowed_host or host.endswith("." + allowed_host):
            return True
    return False


_V4A_PATCH_PATH_RE = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s+(.+?)\s*$", re.M)


def _tool_paths(tool_name: str, args: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    base_dir = str(args.get("workdir") or args.get("cwd") or "")
    for key in ("path", "file_path", "local_path", "remote_path", "workdir", "cwd"):
        value = args.get(key)
        if value:
            paths.append(str(value))
    if tool_name == "terminal":
        cmd = str(args.get("command") or "")
        for match in re.findall(r"(?:^|\s)(/[\w./-]+|~/?[\w./-]*|[\w.-]+/[\w./-]+)", cmd):
            paths.append(match if match.startswith(("/", "~")) or not base_dir else str(Path(os.path.expanduser(base_dir)) / match))
    if tool_name == "patch" and str(args.get("mode") or "replace") == "patch":
        paths.extend(p.strip() for p in _V4A_PATCH_PATH_RE.findall(str(args.get("patch") or "")) if p.strip())
    return paths


def _tool_urls(tool_name: str, args: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("url", "urls", "locator"):
        value = args.get(key)
        if isinstance(value, list):
            urls.extend(str(x) for x in value if x)
        elif value:
            urls.append(str(value))
    if tool_name == "terminal":
        cmd = str(args.get("command") or "")
        urls.extend(re.findall(r"https?://[^\s)\]}>\"']+", cmd, flags=re.I))
        for match in re.findall(r"\b(?:curl|wget|httpie|http)\s+(?:-[^\s]+\s+)*([^\s;&|]+)", cmd, flags=re.I):
            target = match.strip().strip("'\"")
            if target and not target.startswith(("-", "/", "./", "../")):
                urls.append(target)
    return urls


def _is_destructive(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in {"write_file", "patch", "memory", "skill_manage", "cronjob", "send_message"}:
        return True
    if tool_name == "terminal":
        return bool(re.search(r"\b(rm\s+-rf|mkfs|dd\s+if=|shutdown|reboot|sudo\s+|chmod\s+-r|chown\s+-r)\b", str(args.get("command") or "").lower()))
    return False


def _is_trading_live(tool_name: str, args: dict[str, Any]) -> bool:
    blob = (tool_name + " " + json.dumps(args, ensure_ascii=False, default=str)).lower()
    if not any(k in blob for k in ("trade", "trading", "order", "bitget", "binance", "buy", "sell", "futures")):
        return False
    if any(k in blob for k in ("dry_run", "dry-run", "paper", "simulate", "backtest")):
        return False
    return any(k in blob for k in ("place", "submit", "create_order", "live", "buy", "sell"))


class PolicyGuard:
    @staticmethod
    def pre_tool_call(tool_name: str, args: dict[str, Any], contract: PermissionContract, *, mode: str = "supervised") -> PolicyGuardResult:
        if mode == "full_auto":
            return PolicyGuardResult()
        if not isinstance(args, dict):
            return PolicyGuardResult("deny", "tool arguments must be an object")
        if _is_trading_live(tool_name, args) and contract.trading_mode != "live_allowed":
            return PolicyGuardResult("deny", f"live trading is not allowed by contract ({contract.trading_mode})")
        if contract.destructive_actions == "deny" and _is_destructive(tool_name, args):
            return PolicyGuardResult("deny", f"destructive tool {tool_name} is denied")
        base_dir = str(args.get("workdir") or args.get("cwd") or "")
        for path in _tool_paths(tool_name, args):
            if not _path_allowed(path, contract.filesystem_allowlist, base_dir=base_dir):
                return PolicyGuardResult("deny", f"path {path} is outside filesystem allowlist")
        for url in _tool_urls(tool_name, args):
            if not _host_allowed(url, contract.network_allowlist):
                return PolicyGuardResult("deny", f"network target {url} is outside network allowlist")
        return PolicyGuardResult()


class ToolHookHandler:
    def __init__(self, store: SupergoalStore) -> None:
        self.store = store

    def _load_state(self, session_id: str) -> tuple[str, GoalState | None]:
        goal_run_id = self.store.get_goal_run_id(session_id)
        if not goal_run_id or not self.store.is_current_session(session_id):
            return "", None
        raw = self.store.load_run(goal_run_id)
        if not raw:
            return goal_run_id, None
        return goal_run_id, GoalState.from_json(json.dumps(raw, ensure_ascii=False))

    def pre_tool_call(self, *, tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> dict[str, str] | None:
        if not session_id:
            return None
        try:
            _goal_run_id, state = self._load_state(session_id)
            if state is None or state.status != "active":
                return None
            mode = str(getattr(state, "permission_mode", "supervised") or "supervised").strip().lower()
            contract = PermissionContract.from_mapping(getattr(state, "permission_contract", {}) or {})
            result = PolicyGuard.pre_tool_call(tool_name, args if isinstance(args, dict) else {}, contract, mode=mode)
            if result.allows_execution:
                return None
            return {"action": "block", "message": result.block_message()}
        except Exception as exc:
            return {"action": "block", "message": json.dumps({"error": f"Supergoal policy deny: {exc}"}, ensure_ascii=False)}

    def post_tool_call(
        self,
        *,
        tool_name: str = "",
        args: Any = None,
        result: Any = None,
        session_id: str = "",
        tool_call_id: str = "",
        turn_id: str = "",
        status: str = "",
        error_type: str | None = None,
        **_: Any,
    ) -> None:
        try:
            goal_run_id, state = self._load_state(session_id)
            if not goal_run_id or state is None:
                return
            call_id = str(tool_call_id or "").strip()
            if not call_id:
                return
            safe_args = redact_sensitive(args if isinstance(args, dict) else {})
            safe_result = redact_sensitive(result)
            ref = evidence_ref_from_tool_call(
                goal_run_id=goal_run_id,
                turn_id=str(turn_id or ""),
                tool_name=tool_name,
                args=safe_args if isinstance(safe_args, dict) else {},
                result=safe_result,
                tool_call_id=call_id,
                status=status or ("error" if error_type else "ok"),
            )
            if ref is None:
                return
            self.store.append_event_once(
                goal_run_id,
                {
                    "type": "tool_evidence_observed",
                    "turn": int(getattr(state, "turns_used", 0) or 0),
                    "ts": time.time(),
                    "summary": ref.claim,
                    "data": {"evidence_ref": ref.to_dict()},
                },
                source_key=f"tool:{call_id}",
            )
        except Exception:
            return
