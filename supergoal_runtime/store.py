"""Independent SQLite persistence for Supergoal mission state."""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .config import get_state_db_path

SCHEMA_VERSION = 2


class StoreError(RuntimeError):
    """Base error for the plugin-owned state store."""


class BindingConflictError(StoreError):
    """A physical session is already bound to another logical run."""


class RunConflictError(StoreError):
    """A legacy import would overwrite a plugin-owned logical run."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    goal_run_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    state_schema_version INTEGER NOT NULL DEFAULT 1,
    legacy_source_key TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS session_bindings (
    session_id TEXT PRIMARY KEY,
    goal_run_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    is_current INTEGER NOT NULL DEFAULT 1,
    bound_at REAL NOT NULL,
    FOREIGN KEY (goal_run_id) REFERENCES runs(goal_run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_json TEXT NOT NULL,
    observed_at REAL NOT NULL,
    legacy_source_key TEXT,
    legacy_source_index INTEGER,
    FOREIGN KEY (goal_run_id) REFERENCES runs(goal_run_id) ON DELETE CASCADE,
    UNIQUE (goal_run_id, legacy_source_key, legacy_source_index)
);

CREATE INDEX IF NOT EXISTS idx_bindings_goal_run
    ON session_bindings(goal_run_id);
CREATE INDEX IF NOT EXISTS idx_events_goal_run
    ON events(goal_run_id, observed_at, id);
"""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_state(state: Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(state, str):
        decoded = json.loads(state)
    else:
        decoded = dict(state)
    if not isinstance(decoded, dict):
        raise ValueError("run state must be a JSON object")
    return decoded


def _normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(event)
    event_type = str(normalized.get("type") or "").strip()
    if not event_type:
        raise ValueError("event type must not be empty")
    normalized["type"] = event_type
    try:
        normalized["turn"] = int(normalized.get("turn", 0) or 0)
    except (TypeError, ValueError):
        normalized["turn"] = 0
    try:
        normalized["ts"] = float(normalized.get("ts", time.time()))
    except (TypeError, ValueError):
        normalized["ts"] = time.time()
    normalized["summary"] = str(normalized.get("summary") or "")
    if not isinstance(normalized.get("data"), dict):
        normalized["data"] = {}
    return normalized


class SupergoalStore:
    """Small profile-scoped repository backed by plugin-owned SQLite.

    The constructor resolves the active profile path but does not create or open
    the database. Schema initialization is lazy on the first real operation, so
    plugin discovery remains side-effect free.
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        hermes_home: str | Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        if db_path is not None and hermes_home is not None:
            raise ValueError("pass either db_path or hermes_home, not both")
        self.db_path = (
            Path(db_path).expanduser().resolve()
            if db_path is not None
            else get_state_db_path(hermes_home)
        )
        self.timeout = float(timeout)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.timeout,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={max(1, int(self.timeout * 1000))}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._transaction() as conn:
            # sqlite3.executescript() implicitly commits any active transaction.
            # Execute these simple DDL statements individually so schema creation
            # and the version marker stay inside this explicit transaction.
            for statement in _SCHEMA_SQL.split(";"):
                sql = statement.strip()
                if sql:
                    conn.execute(sql)
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
            current = int(row[0]) if row and str(row[0]).isdigit() else 0
            if current > SCHEMA_VERSION:
                raise StoreError(
                    f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            binding_columns = {
                str(item[1])
                for item in conn.execute(
                    "PRAGMA table_info(session_bindings)"
                ).fetchall()
            }
            if "is_current" not in binding_columns:
                conn.execute(
                    "ALTER TABLE session_bindings "
                    "ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1"
                )
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def connection_pragmas(self) -> dict[str, Any]:
        self.ensure_schema()
        conn = self._connect()
        try:
            return {
                "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
                "foreign_keys": int(conn.execute("PRAGMA foreign_keys").fetchone()[0]),
                "synchronous": int(conn.execute("PRAGMA synchronous").fetchone()[0]),
            }
        finally:
            conn.close()

    def peek_meta(self, key: str) -> str | None:
        """Read metadata without creating or migrating the database."""

        if not self.db_path.exists():
            return None
        from urllib.parse import quote

        uri = f"file:{quote(str(self.db_path), safe='/')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout)
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='schema_meta'"
            ).fetchone()
            if not table:
                return None
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key=?", (str(key),)
            ).fetchone()
            return str(row[0]) if row else None
        finally:
            conn.close()

    def get_meta(self, key: str) -> str | None:
        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key=?", (str(key),)
            ).fetchone()
            return str(row[0]) if row else None
        finally:
            conn.close()

    def set_meta(self, key: str, value: str) -> None:
        self.ensure_schema()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(key), str(value)),
            )

    @staticmethod
    def _upsert_run(
        conn: sqlite3.Connection,
        goal_run_id: str,
        state: Mapping[str, Any] | str,
        *,
        legacy_source_key: str | None = None,
    ) -> dict[str, Any]:
        run_id = str(goal_run_id or "").strip()
        if not run_id:
            raise ValueError("goal_run_id must not be empty")
        normalized = _normalize_state(state)
        normalized["goal_run_id"] = run_id
        raw = _canonical_json(normalized)
        now = time.time()
        conn.execute(
            """
            INSERT INTO runs(
                goal_run_id, state_json, status, state_schema_version,
                legacy_source_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(goal_run_id) DO UPDATE SET
                state_json=excluded.state_json,
                status=excluded.status,
                state_schema_version=excluded.state_schema_version,
                legacy_source_key=COALESCE(excluded.legacy_source_key, runs.legacy_source_key),
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                raw,
                str(normalized.get("status") or ""),
                int(normalized.get("schema_version", 1) or 1),
                legacy_source_key,
                now,
                now,
            ),
        )
        return normalized

    def save_run(
        self,
        goal_run_id: str,
        state: Mapping[str, Any] | str,
        *,
        legacy_source_key: str | None = None,
    ) -> None:
        self.ensure_schema()
        with self._transaction() as conn:
            self._upsert_run(
                conn,
                goal_run_id,
                state,
                legacy_source_key=legacy_source_key,
            )

    def load_run(self, goal_run_id: str) -> dict[str, Any] | None:
        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_json FROM runs WHERE goal_run_id=?",
                (str(goal_run_id),),
            ).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()

    @staticmethod
    def _bind_session(
        conn: sqlite3.Connection,
        session_id: str,
        goal_run_id: str,
        *,
        reason: str = "",
        is_current: bool = True,
    ) -> bool:
        sid = str(session_id or "").strip()
        run_id = str(goal_run_id or "").strip()
        if not sid or not run_id:
            raise ValueError("session_id and goal_run_id must not be empty")
        existing = conn.execute(
            "SELECT goal_run_id FROM session_bindings WHERE session_id=?", (sid,)
        ).fetchone()
        if existing and str(existing[0]) != run_id:
            raise BindingConflictError(
                f"session {sid!r} is already bound to {existing[0]!r}"
            )
        conn.execute(
            """
            INSERT INTO session_bindings(
                session_id, goal_run_id, reason, is_current, bound_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                reason=excluded.reason,
                is_current=excluded.is_current,
                bound_at=excluded.bound_at
            """,
            (
                sid,
                run_id,
                str(reason or ""),
                1 if is_current else 0,
                time.time(),
            ),
        )
        return existing is None

    def bind_session(
        self, session_id: str, goal_run_id: str, *, reason: str = ""
    ) -> None:
        self.ensure_schema()
        with self._transaction() as conn:
            self._bind_session(conn, session_id, goal_run_id, reason=reason)

    def get_goal_run_id(self, session_id: str) -> str:
        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT goal_run_id FROM session_bindings WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
            return str(row[0]) if row else ""
        finally:
            conn.close()

    def is_current_session(self, session_id: str) -> bool:
        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT is_current FROM session_bindings WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
            return bool(row and int(row[0]))
        finally:
            conn.close()

    def rotate_session_binding(
        self,
        old_session_id: str,
        new_session_id: str,
        *,
        reason: str = "compression",
    ) -> str:
        """Atomically make *new_session_id* the current binding for a run."""

        self.ensure_schema()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT goal_run_id FROM session_bindings WHERE session_id=?",
                (str(old_session_id),),
            ).fetchone()
            if not row:
                return ""
            goal_run_id = str(row[0])
            conn.execute(
                "UPDATE session_bindings SET is_current=0 WHERE session_id=?",
                (str(old_session_id),),
            )
            self._bind_session(
                conn,
                new_session_id,
                goal_run_id,
                reason=reason,
                is_current=True,
            )
            return goal_run_id

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        goal_run_id: str,
        event: Mapping[str, Any],
        *,
        legacy_source_key: str | None = None,
        legacy_source_index: int | None = None,
    ) -> bool:
        normalized = _normalize_event(event)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO events(
                goal_run_id, event_type, event_json, observed_at,
                legacy_source_key, legacy_source_index
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                goal_run_id,
                normalized["type"],
                _canonical_json(normalized),
                normalized["ts"],
                legacy_source_key,
                legacy_source_index,
            ),
        )
        return cursor.rowcount > 0

    def append_event(self, goal_run_id: str, event: Mapping[str, Any]) -> None:
        self.ensure_schema()
        with self._transaction() as conn:
            if not conn.execute(
                "SELECT 1 FROM runs WHERE goal_run_id=?", (goal_run_id,)
            ).fetchone():
                raise StoreError(f"unknown goal run {goal_run_id!r}")
            self._insert_event(conn, goal_run_id, event)

    def append_event_once(
        self,
        goal_run_id: str,
        event: Mapping[str, Any],
        *,
        source_key: str,
        source_index: int = 0,
    ) -> bool:
        self.ensure_schema()
        with self._transaction() as conn:
            if not conn.execute(
                "SELECT 1 FROM runs WHERE goal_run_id=?", (goal_run_id,)
            ).fetchone():
                raise StoreError(f"unknown goal run {goal_run_id!r}")
            return self._insert_event(
                conn,
                goal_run_id,
                event,
                legacy_source_key=source_key,
                legacy_source_index=source_index,
            )

    def load_events(
        self, goal_run_id: str, *, limit: int = 1000
    ) -> list[dict[str, Any]]:
        self.ensure_schema()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT event_json FROM events
                WHERE goal_run_id=?
                ORDER BY id ASC
                LIMIT ?
                """,
                (str(goal_run_id), max(1, int(limit))),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]
        finally:
            conn.close()

    def save_run_with_events(
        self,
        goal_run_id: str,
        state: Mapping[str, Any] | str,
        events: Sequence[Mapping[str, Any]],
    ) -> None:
        normalized_events = [_normalize_event(event) for event in events]
        self.ensure_schema()
        with self._transaction() as conn:
            self._upsert_run(conn, goal_run_id, state)
            for event in normalized_events:
                self._insert_event(conn, goal_run_id, event)

    def import_run_bundle(
        self,
        goal_run_id: str,
        state: Mapping[str, Any] | str,
        *,
        bindings: Iterable[tuple[str, str]] = (),
        events: Iterable[tuple[Mapping[str, Any], str, int]] = (),
        legacy_source_key: str | None = None,
    ) -> dict[str, int]:
        """Atomically import one legacy run, its bindings, and its events."""

        normalized_events = [
            (_normalize_event(event), source_key, int(source_index))
            for event, source_key, source_index in events
        ]
        normalized_bindings = [
            (str(session_id), str(reason)) for session_id, reason in bindings
        ]
        self.ensure_schema()
        counts = {"runs": 0, "bindings": 0, "events": 0}
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT legacy_source_key FROM runs WHERE goal_run_id=?",
                (goal_run_id,),
            ).fetchone()
            if existing:
                existing_source = existing[0]
                if not legacy_source_key or existing_source != legacy_source_key:
                    raise RunConflictError(
                        "legacy import refused to overwrite an existing run"
                    )
            else:
                self._upsert_run(
                    conn,
                    goal_run_id,
                    state,
                    legacy_source_key=legacy_source_key,
                )
                counts["runs"] = 1
            for session_id, reason in normalized_bindings:
                if self._bind_session(
                    conn, session_id, goal_run_id, reason=reason
                ):
                    counts["bindings"] += 1
            for event, source_key, source_index in normalized_events:
                if self._insert_event(
                    conn,
                    goal_run_id,
                    event,
                    legacy_source_key=source_key,
                    legacy_source_index=source_index,
                ):
                    counts["events"] += 1
        return counts

    def backup(self, destination: str | Path | None = None) -> Path | None:
        """Create a consistent SQLite backup if the database exists."""

        if not self.db_path.exists():
            return None
        if destination is None:
            stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
            destination = self.db_path.with_name(
                f"{self.db_path.name}.backup-{stamp}-{time.time_ns()}"
            )
        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(str(self.db_path), timeout=self.timeout)
        target_conn = sqlite3.connect(str(destination), timeout=self.timeout)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()
        return destination

    def copy_to(self, destination: str | Path) -> Path:
        """Copy an offline DB file; primarily useful for tests and exports."""

        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.db_path, destination)
        return destination
