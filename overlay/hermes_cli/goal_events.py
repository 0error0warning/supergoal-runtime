"""Durable GoalEvent storage for /goal and /supergoal.

This module owns the append-only event log.  Goal state itself still lives in
``goals.py`` for now, but Supergoal projections can rebuild cached board fields
from these events instead of relying solely on critic JSON.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GoalEvent:
    """Append-only audit / observation event for a goal or supergoal run."""

    ts: float
    type: str
    turn: int = 0
    summary: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["GoalEvent"]:
        if not isinstance(data, dict):
            return None
        event_type = str(data.get("type") or "").strip()
        if not event_type:
            return None
        maybe_data = data.get("data")
        raw_data: Dict[str, Any] = maybe_data if isinstance(maybe_data, dict) else {}
        raw_ts = data.get("ts")
        try:
            ts = float(raw_ts) if raw_ts is not None else time.time()
        except Exception:
            ts = time.time()
        return cls(
            ts=ts,
            type=event_type,
            turn=int(data.get("turn", 0) or 0),
            summary=str(data.get("summary") or "").strip(),
            data=raw_data,
        )


def events_meta_key(session_id: str) -> str:
    return f"goal_events:{session_id}"


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "… [truncated]"


def _get_session_db() -> Optional[Any]:
    try:
        from hermes_constants import get_hermes_home
        from hermes_state import SessionDB

        # Keep one DB per HERMES_HOME without importing goals.py.
        home = str(get_hermes_home())
        cache = getattr(_get_session_db, "_cache", {})
        if home in cache:
            return cache[home]
        db = SessionDB()
        cache[home] = db
        setattr(_get_session_db, "_cache", cache)
        return db
    except Exception as exc:  # pragma: no cover - defensive for nonstandard launchers
        logger.debug("GoalEvent store bootstrap failed: %s", exc)
        return None


def load_goal_events(session_id: str, *, limit: int = 100) -> List[GoalEvent]:
    """Load append-only goal events for a session from state_meta."""
    if not session_id:
        return []
    db = _get_session_db()
    if db is None:
        return []
    try:
        raw = db.get_meta(events_meta_key(session_id))
    except Exception as exc:
        logger.debug("GoalEvent store get_meta failed: %s", exc)
        return []
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except Exception as exc:
        logger.debug("GoalEvent store parse failed for %s: %s", session_id, exc)
        return []
    if not isinstance(decoded, list):
        return []
    events: List[GoalEvent] = []
    for item in decoded[-max(1, int(limit or 100)):]:
        event = GoalEvent.from_dict(item)
        if event is not None:
            events.append(event)
    return events


def append_goal_event(
    session_id: str,
    event_type: str,
    *,
    turn: int = 0,
    summary: str = "",
    data: Optional[Dict[str, Any]] = None,
    max_events: int = 200,
) -> Optional[GoalEvent]:
    """Append an event to the durable event log."""
    if not session_id or not event_type:
        return None
    db = _get_session_db()
    if db is None:
        return None
    event = GoalEvent(
        ts=time.time(),
        type=str(event_type).strip(),
        turn=int(turn or 0),
        summary=_truncate(" ".join(str(summary or "").split()), 500),
        data=data or {},
    )
    events = load_goal_events(session_id, limit=max_events)
    events.append(event)
    events = events[-max_events:]
    try:
        db.set_meta(
            events_meta_key(session_id),
            json.dumps([e.to_dict() for e in events], ensure_ascii=False),
        )
    except Exception as exc:
        logger.debug("GoalEvent store set_meta failed: %s", exc)
    return event
