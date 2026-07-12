from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from supergoal_runtime.config import get_state_db_path
from supergoal_runtime.store import (
    BindingConflictError,
    StoreError,
    SupergoalStore,
)


def test_store_uses_profile_scoped_path_and_isolates_profiles(tmp_path, monkeypatch):
    home_a = tmp_path / "profiles" / "alpha"
    home_b = tmp_path / "profiles" / "beta"

    monkeypatch.setenv("HERMES_HOME", str(home_a))
    store_a = SupergoalStore()
    store_a.save_run("run-a", {"goal": "alpha", "status": "active"})
    store_a.bind_session("session-a", "run-a", reason="start")

    monkeypatch.setenv("HERMES_HOME", str(home_b))
    store_b = SupergoalStore()
    store_b.save_run("run-b", {"goal": "beta", "status": "paused"})
    store_b.bind_session("session-b", "run-b", reason="start")

    assert store_a.db_path == home_a / "supergoal" / "state.db"
    assert store_b.db_path == home_b / "supergoal" / "state.db"
    assert store_a.load_run("run-b") is None
    assert store_b.load_run("run-a") is None
    assert store_a.get_goal_run_id("session-a") == "run-a"
    assert store_b.get_goal_run_id("session-b") == "run-b"


def test_state_path_prefers_context_override_over_environment(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    env_home = tmp_path / "env-profile"
    context_home = tmp_path / "context-profile"
    monkeypatch.setenv("HERMES_HOME", str(env_home))

    token = set_hermes_home_override(context_home)
    try:
        assert get_state_db_path() == context_home / "supergoal" / "state.db"
    finally:
        reset_hermes_home_override(token)

    assert get_state_db_path() == env_home / "supergoal" / "state.db"


def test_concurrent_context_profiles_do_not_cross_write(tmp_path, monkeypatch):
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-fallback"))
    home_a = tmp_path / "context-a"
    home_b = tmp_path / "context-b"

    async def worker(home, run_id):
        token = set_hermes_home_override(home)
        try:
            await asyncio.sleep(0)
            store = SupergoalStore()
            store.save_run(run_id, {"goal": run_id, "status": "active"})
            return store.db_path
        finally:
            reset_hermes_home_override(token)

    async def run_workers():
        return await asyncio.gather(
            worker(home_a, "run-a"),
            worker(home_b, "run-b"),
        )

    path_a, path_b = asyncio.run(run_workers())
    store_a = SupergoalStore(db_path=path_a)
    store_b = SupergoalStore(db_path=path_b)

    assert path_a == home_a / "supergoal" / "state.db"
    assert path_b == home_b / "supergoal" / "state.db"
    assert store_a.load_run("run-a") is not None
    assert store_a.load_run("run-b") is None
    assert store_b.load_run("run-b") is not None
    assert store_b.load_run("run-a") is None


def test_store_initializes_required_schema_and_pragmas(tmp_path):
    db_path = tmp_path / "home" / "supergoal" / "state.db"
    store = SupergoalStore(db_path=db_path)
    store.ensure_schema()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        schema_version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]

    assert {"schema_meta", "runs", "session_bindings", "events"} <= tables
    assert journal_mode.lower() == "wal"
    # PRAGMA foreign_keys is connection-local. The store's own connection contract
    # is verified separately; a fresh sqlite3 connection defaults to 0.
    assert foreign_keys == 0
    assert int(schema_version) >= 1
    assert store.connection_pragmas()["foreign_keys"] == 1


def test_foreign_key_is_enforced_on_every_store_connection(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")

    with pytest.raises(sqlite3.IntegrityError):
        store.bind_session("orphan-session", "missing-run")


def test_future_schema_version_is_rejected_without_downgrade(tmp_path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', '999')"
        )

    store = SupergoalStore(db_path=db_path)
    with pytest.raises(StoreError, match="newer than supported"):
        store.ensure_schema()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
    assert version == "999"


def test_schema_initialization_preserves_existing_data(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    store.save_run_with_events(
        "run-preserved",
        {"goal": "preserve", "status": "active"},
        [{"type": "created", "ts": 1}],
    )

    store.ensure_schema()
    store.ensure_schema()

    assert store.load_run("run-preserved")["goal"] == "preserve"
    assert [item["type"] for item in store.load_events("run-preserved")] == [
        "created"
    ]


def test_session_binding_is_unique_and_conflicts_fail_closed(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    store.save_run("run-1", {"goal": "one", "status": "active"})
    store.save_run("run-2", {"goal": "two", "status": "active"})
    store.bind_session("session", "run-1", reason="start")

    with pytest.raises(BindingConflictError):
        store.bind_session("session", "run-2", reason="compression")

    assert store.get_goal_run_id("session") == "run-1"


def test_save_run_and_events_commit_atomically(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")

    with pytest.raises(ValueError, match="event type"):
        store.save_run_with_events(
            "run-1",
            {"goal": "atomic", "status": "active", "turns_used": 1},
            [{"type": "turn_started"}, {"type": ""}],
        )

    assert store.load_run("run-1") is None
    assert store.load_events("run-1") == []

    store.save_run_with_events(
        "run-1",
        {"goal": "atomic", "status": "active", "turns_used": 1},
        [{"type": "turn_started", "turn": 1, "summary": "started"}],
    )
    assert store.load_run("run-1")["turns_used"] == 1
    assert [event["type"] for event in store.load_events("run-1")] == [
        "turn_started"
    ]


def test_event_ledger_preserves_append_order_not_timestamp_order(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    store.save_run_with_events(
        "run-order",
        {"goal": "order", "status": "active"},
        [
            {"type": "first", "ts": 20},
            {"type": "second", "ts": 10},
        ],
    )

    assert [item["type"] for item in store.load_events("run-order")] == [
        "first",
        "second",
    ]


def test_store_migrates_v1_binding_schema_idempotently(tmp_path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('schema_version', '1');
            CREATE TABLE runs (
                goal_run_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '',
                state_schema_version INTEGER NOT NULL DEFAULT 1,
                legacy_source_key TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE session_bindings (
                session_id TEXT PRIMARY KEY,
                goal_run_id TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                bound_at REAL NOT NULL,
                FOREIGN KEY (goal_run_id) REFERENCES runs(goal_run_id) ON DELETE CASCADE
            );
            """
        )

    store = SupergoalStore(db_path=db_path)
    store.ensure_schema()
    store.ensure_schema()

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(session_bindings)")
        }
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]

    assert "is_current" in columns
    assert int(version) == 2


def test_store_persists_canonical_json_not_python_repr(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    state = {"goal": "中文", "status": "active", "nested": {"b": 2, "a": 1}}
    store.save_run("run-json", state)

    with sqlite3.connect(store.db_path) as conn:
        raw = conn.execute(
            "SELECT state_json FROM runs WHERE goal_run_id='run-json'"
        ).fetchone()[0]

    decoded = json.loads(raw)
    assert decoded == {**state, "goal_run_id": "run-json"}
    assert "中文" in raw
