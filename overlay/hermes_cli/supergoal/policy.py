"""Pre-tool permission policy for unattended /supergoal runs."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlparse


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
        if self.allows_execution:
            return ""
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


def _task_base_dir(task_id: str = "") -> str:
    try:
        from tools.file_tools import _resolve_base_dir

        return str(_resolve_base_dir(task_id or "default"))
    except Exception:
        raw = os.getenv("TERMINAL_CWD", "")
        if raw and os.path.isabs(os.path.expanduser(raw)):
            return os.path.expanduser(raw)
        try:
            return os.getcwd()
        except Exception:
            return ""


_V4A_PATCH_PATH_RE = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s+(.+?)\s*$", re.M)


def _tool_paths(tool_name: str, args: dict[str, Any], *, task_id: str = "") -> list[str]:
    paths: list[str] = []
    base_dir = str(args.get("workdir") or args.get("cwd") or _task_base_dir(task_id) or "")
    for key in ("path", "file_path", "local_path", "remote_path", "workdir", "cwd"):
        value = args.get(key)
        if value:
            paths.append(str(value))
    if tool_name == "terminal":
        # Conservative extraction for common absolute/relative file targets.
        # Relative matches are resolved against the terminal's own workdir/cwd,
        # not the Hermes process cwd, so allowlists do not falsely block
        # `terminal(command='cat data/x', workdir='/allowed')`.
        cmd = str(args.get("command") or "")
        for match in re.findall(r"(?:^|\s)(/[\w./-]+|~/?[\w./-]*|[\w.-]+/[\w./-]+)", cmd):
            if match.startswith(("/", "~")) or not base_dir:
                paths.append(match)
            else:
                paths.append(str(Path(os.path.expanduser(base_dir)) / match))
    if tool_name == "patch" and str(args.get("mode") or "replace") == "patch":
        patch_text = str(args.get("patch") or "")
        paths.extend(p.strip() for p in _V4A_PATCH_PATH_RE.findall(patch_text) if p.strip())
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
        # Catch common network CLI forms without schemes: curl example.com,
        # wget api.example/v1. Avoid treating shell flags or local paths as hosts.
        for match in re.findall(r"\b(?:curl|wget|httpie|http)\s+(?:-[^\s]+\s+)*([^\s;&|]+)", cmd, flags=re.I):
            target = match.strip().strip("'\"")
            if target and not target.startswith(("-", "/", "./", "../")):
                urls.append(target)
    return urls


def _is_destructive(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in {"write_file", "patch", "memory", "skill_manage", "cronjob", "send_message"}:
        return True
    if tool_name == "terminal":
        cmd = str(args.get("command") or "").lower()
        return bool(re.search(r"\b(rm\s+-rf|mkfs|dd\s+if=|shutdown|reboot|sudo\s+|chmod\s+-r|chown\s+-r)\b", cmd))
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
    def pre_tool_call(goal_run_id: str, action: Any, tool_name: str, args: dict[str, Any], contract: PermissionContract, *, mode: str = "supervised", task_id: str = "") -> PolicyGuardResult:
        if mode == "full_auto":
            return PolicyGuardResult()
        if not isinstance(args, dict):
            args = {}
        if _is_trading_live(tool_name, args) and contract.trading_mode != "live_allowed":
            return PolicyGuardResult("deny", f"live trading is not allowed by contract ({contract.trading_mode})")
        if contract.destructive_actions == "deny" and _is_destructive(tool_name, args):
            return PolicyGuardResult("deny", f"destructive tool {tool_name} is denied")
        # Ask-mode destructive actions are intentionally *not* blocked here:
        # they must fall through to Hermes' existing dangerous-command/edit
        # approval paths. PolicyGuard only enforces hard denies/sandbox-only;
        # it must not convert ask-mode into a synthetic tool error.
        base_dir = str(args.get("workdir") or args.get("cwd") or _task_base_dir(task_id) or "")
        for p in _tool_paths(tool_name, args, task_id=task_id):
            if not _path_allowed(p, contract.filesystem_allowlist, base_dir=base_dir):
                return PolicyGuardResult("deny", f"path {p} is outside filesystem allowlist")
        for u in _tool_urls(tool_name, args):
            if not _host_allowed(u, contract.network_allowlist):
                return PolicyGuardResult("deny", f"network target {u} is outside network allowlist")
        return PolicyGuardResult()


def pre_tool_policy_block_message(session_id: str, tool_name: str, args: dict[str, Any], *, task_id: str = "") -> str | None:
    if not session_id:
        return None
    try:
        from hermes_cli.goals import load_goal

        state = load_goal(session_id)
        if state is None or getattr(state, "mode", "goal") != "supergoal":
            return None
        mode = str(getattr(state, "permission_mode", "supervised") or "supervised").strip().lower()
        contract = PermissionContract.from_mapping(getattr(state, "permission_contract", {}) or {})
        result = PolicyGuard.pre_tool_call(
            getattr(state, "goal_run_id", "") or session_id,
            getattr(state, "action_proposal", None),
            tool_name,
            args,
            contract,
            mode=mode,
            task_id=task_id,
        )
        return None if result.allows_execution else result.block_message()
    except Exception:
        return None
