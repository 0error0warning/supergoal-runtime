"""Storage primitives for logical /supergoal runs.

A physical Hermes ``session_id`` is a context version.  A long-running
/supergoal mission needs a stable logical identity, represented by
``goal_run_id``.  This module owns the small state_meta key scheme that maps
between them without importing the large ``hermes_cli.goals`` facade.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_CACHE: dict[str, Any] = {}


def goal_session_key(session_id: str) -> str:
    return f"goal_session:{session_id}"


def goal_run_key(goal_run_id: str) -> str:
    return f"goal_run:{goal_run_id}"


def legacy_goal_key(session_id: str) -> str:
    return f"goal:{session_id}"


def new_goal_run_id() -> str:
    return f"gr_{uuid.uuid4().hex[:16]}"


def legacy_goal_run_id(session_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "")
    return f"legacy-{clean or uuid.uuid4().hex[:12]}"


def get_session_db() -> Optional[Any]:
    """Return a SessionDB instance for the current HERMES_HOME/profile."""
    try:
        from hermes_constants import get_hermes_home
        from hermes_state import SessionDB

        home = str(get_hermes_home())
    except Exception as exc:  # pragma: no cover - defensive for nonstandard launchers
        logger.debug("Supergoal store bootstrap failed: %s", exc)
        return None

    cached = _DB_CACHE.get(home)
    if cached is not None:
        return cached
    try:
        db = SessionDB()
    except Exception as exc:  # pragma: no cover
        logger.debug("Supergoal store SessionDB() raised: %s", exc)
        return None
    _DB_CACHE[home] = db
    return db


def get_meta(key: str) -> str:
    db = get_session_db()
    if db is None or not key:
        return ""
    try:
        return str(db.get_meta(key) or "")
    except Exception as exc:
        logger.debug("Supergoal store get_meta(%s) failed: %s", key, exc)
        return ""


def set_meta(key: str, value: str) -> None:
    db = get_session_db()
    if db is None or not key:
        return
    try:
        db.set_meta(key, value)
    except Exception as exc:
        logger.debug("Supergoal store set_meta(%s) failed: %s", key, exc)


class SessionBindingStore:
    """Maps physical session ids to logical goal_run ids."""

    def get_goal_run_id(self, session_id: str) -> str:
        if not session_id:
            return ""
        return get_meta(goal_session_key(session_id)).strip()

    def bind(self, session_id: str, goal_run_id: str, *, reason: str = "") -> None:
        if not session_id or not goal_run_id:
            return
        set_meta(goal_session_key(session_id), goal_run_id)


class GoalRunRepository:
    """Raw JSON repository for logical goal runs.

    The concrete ``GoalState`` dataclass still lives in ``hermes_cli.goals``
    during the facade migration.  Keeping this repository raw avoids circular
    imports while moving persistence responsibility out of GoalManager.
    """

    def get_raw(self, goal_run_id: str) -> str:
        if not goal_run_id:
            return ""
        return get_meta(goal_run_key(goal_run_id))

    def set_raw(self, goal_run_id: str, raw_json: str) -> None:
        if not goal_run_id:
            return
        set_meta(goal_run_key(goal_run_id), raw_json)

    def get_legacy_raw(self, session_id: str) -> str:
        if not session_id:
            return ""
        return get_meta(legacy_goal_key(session_id))

    def set_legacy_raw(self, session_id: str, raw_json: str) -> None:
        if not session_id:
            return
        set_meta(legacy_goal_key(session_id), raw_json)
