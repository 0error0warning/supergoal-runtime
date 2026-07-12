"""Compatibility adapter for detecting ordinary Hermes /goal activity."""

from __future__ import annotations

from typing import Any


def ordinary_goal_active(session_id: str) -> bool:
    if not session_id:
        return False
    try:
        from hermes_cli.goals import load_goal
    except Exception:
        return False
    try:
        state: Any = load_goal(session_id)
    except Exception:
        return False
    return bool(
        state is not None
        and getattr(state, "status", "") == "active"
        and getattr(state, "mode", "goal") != "supergoal"
    )
