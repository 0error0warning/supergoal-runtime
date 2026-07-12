"""Platform-neutral Supergoal command and status rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StatusCard:
    goal_run_id: str
    status: str
    goal: str
    turns: str
    line: str
    details: list[str]

    def to_text(self) -> str:
        parts = [self.line]
        parts.extend(self.details)
        return "\n".join(part for part in parts if part)


def status_line(state: Any | None) -> str:
    if state is None:
        return "No active /sgx supergoal for this session."
    turns = f"{int(getattr(state, 'turns_used', 0) or 0)}/{int(getattr(state, 'max_turns', 0) or 0)}"
    goal = str(getattr(state, "goal", "") or "").strip()
    status = str(getattr(state, "status", "") or "unknown")
    reason = str(getattr(state, "paused_reason", "") or getattr(state, "last_reason", "") or "")
    suffix = f" - {reason}" if reason else ""
    return f"Supergoal {status} ({turns} turns): {goal}{suffix}"


def status_card(state: Any | None) -> StatusCard:
    if state is None:
        return StatusCard("", "inactive", "", "0/0", status_line(None), [])
    details: list[str] = []
    if getattr(state, "next_best_action", ""):
        details.append(f"Next: {getattr(state, 'next_best_action')}")
    open_gates = [
        f"{getattr(gate, 'id', '')}:{getattr(gate, 'status', '')}"
        for gate in (getattr(state, "gates", []) or [])
        if getattr(gate, "status", "") != "passed"
    ]
    if open_gates:
        details.append("Open gates: " + ", ".join(open_gates[:8]))
    if getattr(state, "waiting_on_pid", None) is not None:
        details.append(f"Waiting on pid {getattr(state, 'waiting_on_pid')}")
    if getattr(state, "waiting_on_session", None):
        details.append(f"Waiting on session {getattr(state, 'waiting_on_session')}")
    turns = f"{int(getattr(state, 'turns_used', 0) or 0)}/{int(getattr(state, 'max_turns', 0) or 0)}"
    return StatusCard(
        goal_run_id=str(getattr(state, "goal_run_id", "") or ""),
        status=str(getattr(state, "status", "") or "unknown"),
        goal=str(getattr(state, "goal", "") or ""),
        turns=turns,
        line=status_line(state),
        details=details,
    )


def command_result(text: str) -> str:
    return str(text or "")
