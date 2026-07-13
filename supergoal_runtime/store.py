"""Independent SQLite persistence for Supergoal mission state."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .config import get_state_db_path

SCHEMA_VERSION = 2
OUTBOX_CAPABILITY = "continuation_outbox_v1"


class StoreError(RuntimeError):
    """Base error for the plugin-owned state store."""


class BindingConflictError(StoreError):
    """A physical session is already bound to another logical run."""


class RunConflictError(StoreError):
    """A legacy import would overwrite a plugin-owned logical run."""


class RevisionConflictError(StoreError):
    """A run changed after a caller loaded its state snapshot."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    goal_run_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS continuation_outbox (
    token TEXT PRIMARY KEY,
    goal_run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'continue',
    state_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    claimed_at REAL,
    claim_owner TEXT NOT NULL DEFAULT '',
    consumed_at REAL,
    cancelled_at REAL,
    cancel_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (goal_run_id) REFERENCES runs(goal_run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bindings_goal_run
    ON session_bindings(goal_run_id);
CREATE INDEX IF NOT EXISTS idx_events_goal_run
    ON events(goal_run_id, observed_at, id);
CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON continuation_outbox(status, created_at, token);
CREATE INDEX IF NOT EXISTS idx_outbox_goal_run
    ON continuation_outbox(goal_run_id, status);
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
        self._schema_ready = False
        self._schema_lock = threading.Lock()

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
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._transaction() as conn:
                # sqlite3.executescript() implicitly commits any active
                # transaction. Execute the simple DDL statements individually
                # so schema creation and markers remain atomic.
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
                run_columns = {
                    str(item[1])
                    for item in conn.execute("PRAGMA table_info(runs)").fetchall()
                }
                if "revision" not in run_columns:
                    conn.execute(
                        "ALTER TABLE runs "
                        "ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                    )
                outbox_columns = {
                    str(item[1])
                    for item in conn.execute(
                        "PRAGMA table_info(continuation_outbox)"
                    ).fetchall()
                }
                if "claimed_at" not in outbox_columns:
                    conn.execute(
                        "ALTER TABLE continuation_outbox ADD COLUMN claimed_at REAL"
                    )
                if "claim_owner" not in outbox_columns:
                    conn.execute(
                        "ALTER TABLE continuation_outbox "
                        "ADD COLUMN claim_owner TEXT NOT NULL DEFAULT ''"
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
                conn.execute(
                    "INSERT INTO schema_meta(key, value) "
                    "VALUES('capability:continuation_outbox', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (OUTBOX_CAPABILITY,),
                )
            self._schema_ready = True

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
                goal_run_id, state_json, status, revision, state_schema_version,
                legacy_source_key, created_at, updated_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(goal_run_id) DO UPDATE SET
                state_json=excluded.state_json,
                status=excluded.status,
                revision=runs.revision + 1,
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

    @staticmethod
    def _cancel_pending_continuations(
        conn: sqlite3.Connection,
        *,
        goal_run_id: str | None = None,
        session_id: str | None = None,
        reason: str,
    ) -> int:
        clauses = ["status IN ('pending', 'claimed')"]
        params: list[Any] = [time.time(), str(reason or "cancelled")]
        if goal_run_id is not None:
            clauses.append("goal_run_id=?")
            params.append(str(goal_run_id))
        if session_id is not None:
            clauses.append("session_id=?")
            params.append(str(session_id))
        cursor = conn.execute(
            "UPDATE continuation_outbox "
            "SET status='cancelled', cancelled_at=?, cancel_reason=? "
            f"WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        return max(0, int(cursor.rowcount))

    @staticmethod
    def _settle_continuations(
        conn: sqlite3.Connection,
        *,
        goal_run_id: str,
        reason: str,
    ) -> None:
        """Consume the dispatched item and cancel anything never dispatched."""

        now = time.time()
        conn.execute(
            "UPDATE continuation_outbox "
            "SET status='consumed', consumed_at=? "
            "WHERE goal_run_id=? AND status='claimed'",
            (now, str(goal_run_id)),
        )
        conn.execute(
            "UPDATE continuation_outbox "
            "SET status='cancelled', cancelled_at=?, cancel_reason=? "
            "WHERE goal_run_id=? AND status='pending'",
            (now, str(reason or "superseded"), str(goal_run_id)),
        )

    @staticmethod
    def _insert_continuation(
        conn: sqlite3.Connection,
        continuation: Mapping[str, Any],
        *,
        goal_run_id: str,
        state_version: int,
    ) -> dict[str, Any]:
        token = str(continuation.get("token") or "").strip()
        session_id = str(continuation.get("session_id") or "").strip()
        prompt = str(continuation.get("prompt") or "")
        kind = str(continuation.get("kind") or "continue").strip() or "continue"
        if not token or not session_id or not prompt.strip():
            raise ValueError("continuation token, session_id, and prompt are required")
        conn.execute(
            """
            INSERT INTO continuation_outbox(
                token, goal_run_id, session_id, prompt, kind, state_version,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                token,
                str(goal_run_id),
                session_id,
                prompt,
                kind,
                int(state_version),
                time.time(),
            ),
        )
        return {
            "session_id": session_id,
            "token": token,
            "prompt": prompt,
            "state_version": int(state_version),
        }

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
            self._cancel_pending_continuations(
                conn,
                goal_run_id=goal_run_id,
                reason="run state superseded",
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

    def load_run_snapshot(
        self, goal_run_id: str
    ) -> tuple[dict[str, Any] | None, int]:
        """Return a run and the revision required for a later CAS commit."""

        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_json, revision FROM runs WHERE goal_run_id=?",
                (str(goal_run_id),),
            ).fetchone()
            if not row:
                return None, -1
            return json.loads(row[0]), int(row[1])
        finally:
            conn.close()

    def load_bound_run_snapshot(
        self, session_id: str
    ) -> tuple[str, dict[str, Any] | None, int]:
        """Load the current run bound to a physical Hermes session."""

        self.ensure_schema()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT r.goal_run_id, r.state_json, r.revision
                FROM session_bindings AS b
                JOIN runs AS r ON r.goal_run_id=b.goal_run_id
                WHERE b.session_id=? AND b.is_current=1
                """,
                (str(session_id),),
            ).fetchone()
            if not row:
                return "", None, -1
            return str(row[0]), json.loads(row[1]), int(row[2])
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
            conn.execute(
                "UPDATE continuation_outbox SET session_id=? "
                "WHERE session_id=? AND goal_run_id=? AND status='pending'",
                (str(new_session_id), str(old_session_id), goal_run_id),
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

    def append_active_event_once_for_session(
        self,
        session_id: str,
        event: Mapping[str, Any],
        *,
        source_key: str,
        source_index: int = 0,
        expected_goal_run_id: str | None = None,
    ) -> bool:
        """Append evidence only while the bound run is transactionally active."""

        normalized = _normalize_event(event)
        self.ensure_schema()
        with self._transaction() as conn:
            clauses = [
                "b.session_id=?",
                "b.is_current=1",
                "r.status='active'",
            ]
            params: list[Any] = [str(session_id)]
            if expected_goal_run_id is not None:
                clauses.append("r.goal_run_id=?")
                params.append(str(expected_goal_run_id))
            row = conn.execute(
                """
                SELECT r.goal_run_id
                FROM session_bindings AS b
                JOIN runs AS r ON r.goal_run_id=b.goal_run_id
                WHERE """ + " AND ".join(clauses),
                tuple(params),
            ).fetchone()
            if not row:
                return False
            return self._insert_event(
                conn,
                str(row[0]),
                normalized,
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
            self._cancel_pending_continuations(
                conn,
                goal_run_id=goal_run_id,
                reason="run state superseded",
            )
            for event in normalized_events:
                self._insert_event(conn, goal_run_id, event)

    def save_run_with_events_cas(
        self,
        goal_run_id: str,
        state: Mapping[str, Any] | str,
        events: Sequence[Mapping[str, Any]],
        *,
        expected_revision: int,
        require_status: str | None = None,
        continuation: Mapping[str, Any] | None = None,
        cancel_pending: bool = False,
        settle_claimed: bool = False,
        cancel_reason: str = "run state superseded",
        deactivate_session_id: str | None = None,
    ) -> tuple[int, dict[str, Any] | None] | None:
        """Commit state, events, and an optional continuation with CAS.

        Returning ``None`` means another lifecycle action won the race. No
        event or outbox row is written in that case.
        """

        normalized = _normalize_state(state)
        normalized["goal_run_id"] = str(goal_run_id)
        normalized_events = [_normalize_event(event) for event in events]
        raw = _canonical_json(normalized)
        self.ensure_schema()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT revision, status FROM runs WHERE goal_run_id=?",
                (str(goal_run_id),),
            ).fetchone()
            if not row or int(row[0]) != int(expected_revision):
                return None
            if require_status is not None and str(row[1]) != str(require_status):
                return None
            new_revision = int(expected_revision) + 1
            cursor = conn.execute(
                """
                UPDATE runs
                SET state_json=?, status=?, revision=?, state_schema_version=?,
                    updated_at=?
                WHERE goal_run_id=? AND revision=?
                """,
                (
                    raw,
                    str(normalized.get("status") or ""),
                    new_revision,
                    int(normalized.get("schema_version", 1) or 1),
                    time.time(),
                    str(goal_run_id),
                    int(expected_revision),
                ),
            )
            if cursor.rowcount != 1:
                return None
            for event in normalized_events:
                self._insert_event(conn, str(goal_run_id), event)
            if settle_claimed:
                self._settle_continuations(
                    conn,
                    goal_run_id=str(goal_run_id),
                    reason=cancel_reason,
                )
            elif cancel_pending or continuation is not None:
                self._cancel_pending_continuations(
                    conn,
                    goal_run_id=str(goal_run_id),
                    reason=cancel_reason,
                )
            if deactivate_session_id is not None:
                conn.execute(
                    "UPDATE session_bindings SET is_current=0 "
                    "WHERE session_id=? AND goal_run_id=?",
                    (str(deactivate_session_id), str(goal_run_id)),
                )
            envelope = None
            if continuation is not None:
                envelope = self._insert_continuation(
                    conn,
                    continuation,
                    goal_run_id=str(goal_run_id),
                    state_version=new_revision,
                )
            return new_revision, envelope

    def enqueue_continuation(
        self,
        goal_run_id: str,
        continuation: Mapping[str, Any],
        *,
        expected_revision: int,
        cancel_reason: str = "newer continuation queued",
    ) -> dict[str, Any] | None:
        """Queue one command continuation without changing run state."""

        self.ensure_schema()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT revision, status FROM runs WHERE goal_run_id=?",
                (str(goal_run_id),),
            ).fetchone()
            if (
                not row
                or int(row[0]) != int(expected_revision)
                or str(row[1]) != "active"
            ):
                return None
            self._cancel_pending_continuations(
                conn,
                goal_run_id=str(goal_run_id),
                reason=cancel_reason,
            )
            return self._insert_continuation(
                conn,
                continuation,
                goal_run_id=str(goal_run_id),
                state_version=int(expected_revision),
            )

    def claim_continuation(
        self,
        *,
        session_id: str,
        token: str,
        claim_owner: str,
        state_version: int | None = None,
    ) -> bool:
        """Claim immediately before dispatch, with restart-safe ownership."""

        sid = str(session_id or "").strip()
        clean_token = str(token or "").strip()
        owner = str(claim_owner or "").strip()
        if not sid or not clean_token or not owner:
            return False
        self.ensure_schema()
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT o.goal_run_id, o.state_version, r.revision, r.status
                FROM continuation_outbox AS o
                JOIN runs AS r ON r.goal_run_id=o.goal_run_id
                JOIN session_bindings AS b
                  ON b.goal_run_id=o.goal_run_id AND b.session_id=o.session_id
                WHERE o.token=? AND o.session_id=?
                  AND o.status IN ('pending', 'claimed')
                  AND (o.status='pending' OR o.claim_owner<>?)
                  AND b.is_current=1
                """,
                (clean_token, sid, owner),
            ).fetchone()
            if not row:
                return False
            outbox_version = int(row[1])
            if state_version is not None and int(state_version) != outbox_version:
                return False
            if str(row[3]) != "active" or int(row[2]) != outbox_version:
                self._cancel_pending_continuations(
                    conn,
                    goal_run_id=str(row[0]),
                    session_id=sid,
                    reason="stale continuation claim",
                )
                return False
            cursor = conn.execute(
                """
                UPDATE continuation_outbox
                SET status='claimed', claimed_at=?, claim_owner=?
                WHERE token=? AND status IN ('pending', 'claimed')
                  AND (status='pending' OR claim_owner<>?)
                """,
                (time.time(), owner, clean_token, owner),
            )
            return cursor.rowcount == 1

    def recover_continuations(self, *, claim_owner: str) -> list[dict[str, Any]]:
        """Return valid pending rows and cancel stale rows transactionally."""

        owner = str(claim_owner or "").strip()
        if not owner:
            return []
        self.ensure_schema()
        with self._transaction() as conn:
            now = time.time()
            conn.execute(
                """
                UPDATE continuation_outbox
                SET status='cancelled', cancelled_at=?,
                    cancel_reason='stale continuation recovery'
                WHERE status IN ('pending', 'claimed') AND NOT EXISTS (
                    SELECT 1
                    FROM runs AS r
                    JOIN session_bindings AS b
                      ON b.goal_run_id=r.goal_run_id
                    WHERE r.goal_run_id=continuation_outbox.goal_run_id
                      AND r.status='active'
                      AND r.revision=continuation_outbox.state_version
                      AND b.session_id=continuation_outbox.session_id
                      AND b.is_current=1
                )
                """,
                (now,),
            )
            rows = conn.execute(
                """
                SELECT session_id, token, prompt, state_version
                FROM continuation_outbox
                WHERE status='pending'
                   OR (status='claimed' AND claim_owner<>?)
                ORDER BY created_at ASC, token ASC
                """,
                (owner,),
            ).fetchall()
            return [
                {
                    "session_id": str(row[0]),
                    "token": str(row[1]),
                    "prompt": str(row[2]),
                    "state_version": int(row[3]),
                }
                for row in rows
            ]

    def cancel_continuations(
        self,
        *,
        goal_run_id: str | None = None,
        session_id: str | None = None,
        reason: str,
    ) -> int:
        self.ensure_schema()
        with self._transaction() as conn:
            return self._cancel_pending_continuations(
                conn,
                goal_run_id=goal_run_id,
                session_id=session_id,
                reason=reason,
            )

    def finalize_session(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any] | None:
        """Pause a live run at a real conversation boundary.

        The old binding stays current so an explicit resume can recover it by
        its durable session id. A newly-created Hermes session has no binding.
        """

        sid = str(session_id or "").strip()
        if not sid:
            return None
        self.ensure_schema()
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT r.goal_run_id, r.state_json, r.revision, r.status
                FROM session_bindings AS b
                JOIN runs AS r ON r.goal_run_id=b.goal_run_id
                WHERE b.session_id=? AND b.is_current=1
                """,
                (sid,),
            ).fetchone()
            if not row:
                self._cancel_pending_continuations(
                    conn,
                    session_id=sid,
                    reason=f"session finalized: {reason}",
                )
                return None
            goal_run_id = str(row[0])
            state = json.loads(row[1])
            if str(row[3]) == "active":
                state["status"] = "paused"
                state["paused_reason"] = f"session finalized: {reason}"
                normalized = _normalize_state(state)
                normalized["goal_run_id"] = goal_run_id
                new_revision = int(row[2]) + 1
                conn.execute(
                    """
                    UPDATE runs
                    SET state_json=?, status='paused', revision=?, updated_at=?
                    WHERE goal_run_id=? AND revision=?
                    """,
                    (
                        _canonical_json(normalized),
                        new_revision,
                        time.time(),
                        goal_run_id,
                        int(row[2]),
                    ),
                )
                self._insert_event(
                    conn,
                    goal_run_id,
                    {
                        "type": "session_finalized",
                        "turn": int(state.get("turns_used", 0) or 0),
                        "ts": time.time(),
                        "summary": str(state["paused_reason"]),
                        "data": {"session_id": sid, "reason": str(reason)},
                    },
                )
            self._cancel_pending_continuations(
                conn,
                goal_run_id=goal_run_id,
                session_id=sid,
                reason=f"session finalized: {reason}",
            )
            return state

    def import_run_bundle(
        self,
        goal_run_id: str,
        state: Mapping[str, Any] | str,
        *,
        bindings: Iterable[tuple[str, str]] = (),
        events: Iterable[tuple[Mapping[str, Any], str, int]] = (),
        legacy_source_key: str | None = None,
        continuation: Mapping[str, Any] | None = None,
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
            if continuation is not None:
                revision_row = conn.execute(
                    "SELECT revision FROM runs WHERE goal_run_id=?",
                    (str(goal_run_id),),
                ).fetchone()
                self._insert_continuation(
                    conn,
                    continuation,
                    goal_run_id=str(goal_run_id),
                    state_version=int(revision_row[0]) if revision_row else 0,
                )
        return counts

    def import_legacy_plan(
        self,
        runs: Mapping[str, tuple[Mapping[str, Any] | str, str | None]],
        *,
        bindings_by_run: Mapping[str, Iterable[tuple[str, str]]] | None = None,
        events_by_run: Mapping[
            str, Iterable[tuple[Mapping[str, Any], str, int]]
        ]
        | None = None,
        marker_key: str,
    ) -> dict[str, Any]:
        """Import a complete migration plan and marker in one transaction."""

        normalized_runs: dict[str, tuple[dict[str, Any], str | None]] = {}
        normalized_bindings: dict[str, list[tuple[str, str]]] = {}
        normalized_events: dict[
            str, list[tuple[dict[str, Any], str, int]]
        ] = {}
        for goal_run_id, (state, source_key) in runs.items():
            run_id = str(goal_run_id or "").strip()
            if not run_id:
                raise ValueError("goal_run_id must not be empty")
            normalized_runs[run_id] = (_normalize_state(state), source_key)
            normalized_bindings[run_id] = [
                (str(session_id), str(reason))
                for session_id, reason in (bindings_by_run or {}).get(run_id, ())
            ]
            normalized_events[run_id] = [
                (_normalize_event(event), str(source_key), int(source_index))
                for event, source_key, source_index in (events_by_run or {}).get(
                    run_id, ()
                )
            ]

        self.ensure_schema()
        totals = {"runs": 0, "bindings": 0, "events": 0}
        per_run: dict[str, dict[str, int]] = {
            run_id: {"runs": 0, "bindings": 0, "events": 0}
            for run_id in normalized_runs
        }
        with self._transaction() as conn:
            for run_id, (_state, source_key) in normalized_runs.items():
                existing = conn.execute(
                    "SELECT legacy_source_key FROM runs WHERE goal_run_id=?",
                    (run_id,),
                ).fetchone()
                if existing and (
                    not source_key or str(existing[0] or "") != str(source_key)
                ):
                    raise RunConflictError(
                        "legacy import refused to overwrite an existing run"
                    )
                for session_id, _reason in normalized_bindings[run_id]:
                    current = conn.execute(
                        "SELECT goal_run_id FROM session_bindings WHERE session_id=?",
                        (session_id,),
                    ).fetchone()
                    if current and str(current[0]) != run_id:
                        raise BindingConflictError(
                            "legacy import found a conflicting session binding"
                        )

            for run_id, (state, source_key) in normalized_runs.items():
                existing = conn.execute(
                    "SELECT 1 FROM runs WHERE goal_run_id=?", (run_id,)
                ).fetchone()
                if not existing:
                    self._upsert_run(
                        conn,
                        run_id,
                        state,
                        legacy_source_key=source_key,
                    )
                    totals["runs"] += 1
                    per_run[run_id]["runs"] += 1
                for session_id, reason in normalized_bindings[run_id]:
                    if self._bind_session(
                        conn, session_id, run_id, reason=reason
                    ):
                        totals["bindings"] += 1
                        per_run[run_id]["bindings"] += 1
                for event, event_source_key, source_index in normalized_events[
                    run_id
                ]:
                    if self._insert_event(
                        conn,
                        run_id,
                        event,
                        legacy_source_key=event_source_key,
                        legacy_source_index=source_index,
                    ):
                        totals["events"] += 1
                        per_run[run_id]["events"] += 1

            marker_value = json.dumps(
                {
                    "status": "migrated",
                    "runs_imported": totals["runs"],
                    "bindings_imported": totals["bindings"],
                    "events_imported": totals["events"],
                    "run_count": len(normalized_runs),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(marker_key), marker_value),
            )

        return {**totals, "per_run": per_run}

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
