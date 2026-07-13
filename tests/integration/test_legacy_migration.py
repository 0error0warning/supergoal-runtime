from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from supergoal_runtime.migration import migrate_legacy_state
from supergoal_runtime.store import SupergoalStore


def _make_legacy_db(
    path: Path,
    rows: dict[str, object],
    *,
    sessions: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO state_meta (key, value) VALUES (?, ?)",
            [
                (
                    key,
                    value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                )
                for key, value in rows.items()
            ],
        )
        if sessions is not None:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            conn.executemany(
                "INSERT INTO sessions(id) VALUES (?)",
                [(session_id,) for session_id in sessions],
            )


def test_legacy_migration_preserves_run_id_bindings_and_event_ledger(tmp_path):
    source = tmp_path / "legacy" / "state.db"
    target = tmp_path / "profile" / "supergoal" / "state.db"
    state = {
        "goal": "migrate me",
        "goal_run_id": "gr_existing",
        "mode": "supergoal",
        "status": "paused",
        "turns_used": 7,
    }
    events = [
        {"ts": 10.0, "type": "set", "turn": 0, "summary": "created", "data": {}},
        {
            "ts": 20.0,
            "type": "session_rotated",
            "turn": 6,
            "summary": "rotated",
            "data": {"old_session_id": "s-old", "new_session_id": "s-new"},
        },
    ]
    _make_legacy_db(
        source,
        {
            "goal_run:gr_existing": state,
            "goal_session:s-old": "gr_existing",
            "goal_session:s-new": "gr_existing",
            "goal:s-old": state,
            "goal_events:gr_existing": events,
        },
    )

    store = SupergoalStore(db_path=target)
    store.ensure_schema()
    store.save_run("preexisting", {"goal": "forces backup", "status": "done"})

    report = migrate_legacy_state(source, store=store, include_paths=True)

    assert report["status"] == "migrated"
    assert report["runs_imported"] == 1
    assert report["bindings_imported"] == 2
    assert report["events_imported"] == 2
    assert report["errors"] == []
    assert report["backup_path"]
    assert Path(report["backup_path"]).exists()
    with sqlite3.connect(report["backup_path"]) as conn:
        backed_up = conn.execute(
            "SELECT state_json FROM runs WHERE goal_run_id='preexisting'"
        ).fetchone()
    assert backed_up is not None
    assert json.loads(backed_up[0])["goal"] == "forces backup"
    assert store.load_run("gr_existing")["turns_used"] == 7
    assert store.get_goal_run_id("s-old") == "gr_existing"
    assert store.get_goal_run_id("s-new") == "gr_existing"
    assert [event["type"] for event in store.load_events("gr_existing")] == [
        "set",
        "session_rotated",
    ]

    # Source is opened read-only and left untouched.
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM state_meta").fetchone()[0] == 5


def test_legacy_session_only_state_gets_stable_run_id_and_is_idempotent(tmp_path):
    source = tmp_path / "legacy.db"
    _make_legacy_db(
        source,
        {
            "goal:session/unsafe": {
                "goal": "legacy",
                "mode": "supergoal",
                "status": "active",
            },
            "goal_events:session/unsafe": [
                {"ts": 1, "type": "set", "turn": 0, "summary": "legacy"}
            ],
        },
    )
    store = SupergoalStore(db_path=tmp_path / "plugin.db")

    first = migrate_legacy_state(source, store=store, backup=False)
    second = migrate_legacy_state(source, store=store, backup=False)

    run_id = store.get_goal_run_id("session/unsafe")
    assert first["status"] == "migrated"
    assert second["status"] == "already_migrated"
    assert run_id.startswith("legacy-session_unsafe-")
    assert store.load_run(run_id)["goal_run_id"] == run_id
    assert len(store.load_events(run_id)) == 1


def test_migration_reports_malformed_rows_without_leaking_values(tmp_path):
    source = tmp_path / "legacy.db"
    secret = "sk-do-not-print-this"
    _make_legacy_db(
        source,
        {
            "goal:broken-session": "{not-json " + secret,
            "goal_events:broken-session": "also-not-json " + secret,
        },
    )
    store = SupergoalStore(db_path=tmp_path / "plugin.db")

    report = migrate_legacy_state(source, store=store, backup=False)
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["status"] == "migration_blocked"
    assert report["errors"]
    assert secret not in rendered
    assert "broken-session" not in rendered
    assert report["run_results"] == []


def test_dry_run_does_not_create_or_modify_target_database(tmp_path):
    source = tmp_path / "legacy.db"
    target = tmp_path / "profile" / "supergoal" / "state.db"
    _make_legacy_db(
        source,
        {
            "goal:session": {
                "goal": "dry",
                "goal_run_id": "gr_dry",
                "mode": "supergoal",
                "status": "active",
            }
        },
    )

    report = migrate_legacy_state(source, target_db=target, dry_run=True)

    assert report["status"] == "dry_run"
    assert report["runs_discovered"] == 1
    assert not target.exists()


def test_backup_happens_before_target_schema_upgrade(tmp_path):
    source = tmp_path / "legacy.db"
    target = tmp_path / "plugin.db"
    _make_legacy_db(
        source,
        {
            "goal:session": {
                "goal": "backup order",
                "goal_run_id": "gr_backup",
                "mode": "supergoal",
                "status": "paused",
            }
        },
    )
    with sqlite3.connect(target) as conn:
        conn.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', '1')"
        )

    report = migrate_legacy_state(source, target_db=target, include_paths=True)

    backup = Path(report["backup_path"])
    with sqlite3.connect(backup) as conn:
        backup_version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        backup_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    with sqlite3.connect(target) as conn:
        target_version = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0]

    assert backup_version == "1"
    assert "runs" not in backup_tables
    assert int(target_version) == 2


def test_import_never_overwrites_existing_live_plugin_run(tmp_path):
    source = tmp_path / "legacy.db"
    _make_legacy_db(
        source,
        {
            "goal_run:gr_shared": {
                "goal": "stale legacy",
                "goal_run_id": "gr_shared",
                "mode": "supergoal",
                "status": "paused",
                "turns_used": 1,
            }
        },
    )
    store = SupergoalStore(db_path=tmp_path / "plugin.db")
    store.save_run(
        "gr_shared",
        {
            "goal": "live plugin state",
            "mode": "supergoal",
            "status": "active",
            "turns_used": 99,
        },
    )

    first = migrate_legacy_state(source, store=store, backup=False)
    second = migrate_legacy_state(source, store=store, backup=False)

    assert first["status"] == "migration_blocked"
    assert second["status"] == "migration_blocked"
    assert first["errors"][0]["error"] == "run_conflict"
    assert store.load_run("gr_shared")["turns_used"] == 99
    assert store.get_meta(first["marker"]) is None


def test_partial_migration_rolls_back_the_entire_plan(tmp_path):
    source = tmp_path / "legacy.db"
    _make_legacy_db(
        source,
        {
            "goal_run:gr_import": {
                "goal": "import",
                "goal_run_id": "gr_import",
                "mode": "supergoal",
                "status": "active",
            },
            "goal_session:shared-session": "gr_import",
            "goal_run:gr_safe": {
                "goal": "must roll back",
                "goal_run_id": "gr_safe",
                "mode": "supergoal",
                "status": "paused",
            },
            "goal_session:safe-session": "gr_safe",
        },
    )
    store = SupergoalStore(db_path=tmp_path / "plugin.db")
    store.save_run("gr_other", {"goal": "other", "status": "active"})
    store.bind_session("shared-session", "gr_other")

    first = migrate_legacy_state(source, store=store, backup=False)
    second = migrate_legacy_state(source, store=store, backup=False)

    assert first["status"] == "migration_blocked"
    assert second["status"] == "migration_blocked"
    assert first["errors"][0]["error"] == "binding_conflict"
    assert store.load_run("gr_import") is None
    assert store.load_run("gr_safe") is None
    assert store.get_goal_run_id("safe-session") == ""
    assert store.get_meta(first["marker"]) is None


def test_success_report_contains_per_run_results_without_raw_run_ids(tmp_path):
    source = tmp_path / "legacy.db"
    run_id = "gr_sensitive_identifier"
    _make_legacy_db(
        source,
        {
            f"goal_run:{run_id}": {
                "goal": "report",
                "goal_run_id": run_id,
                "mode": "supergoal",
                "status": "done",
            }
        },
    )

    target = tmp_path / "plugin.db"
    report = migrate_legacy_state(
        source,
        store=SupergoalStore(db_path=target),
        backup=False,
    )
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["status"] == "migrated"
    assert len(report["run_results"]) == 1
    assert report["run_results"][0]["status"] == "imported"
    assert report["run_results"][0]["run_ref"].startswith("sha256:")
    assert run_id not in rendered
    assert str(source) not in rendered
    assert str(target) not in rendered
    assert "source_path" not in report
    assert "target_path" not in report


def test_real_hermes_source_skips_runs_without_a_surviving_session(tmp_path):
    source = tmp_path / "legacy.db"
    valid_state = {
        "goal": "keep user mission",
        "goal_run_id": "gr_valid",
        "mode": "supergoal",
        "status": "paused",
    }
    orphan_state = {
        "goal": "fixture mission",
        "goal_run_id": "gr_orphan",
        "mode": "supergoal",
        "status": "active",
    }
    _make_legacy_db(
        source,
        {
            "goal_run:gr_valid": valid_state,
            "goal_session:real-session": "gr_valid",
            "goal:real-session": valid_state,
            "goal_run:gr_orphan": orphan_state,
            "goal_session:fixture-session": "gr_orphan",
            "goal:fixture-session": orphan_state,
        },
        sessions=["real-session"],
    )
    store = SupergoalStore(db_path=tmp_path / "plugin.db")

    report = migrate_legacy_state(source, store=store, backup=False)

    assert report["status"] == "migrated"
    assert report["runs_imported"] == 1
    assert report["runs_skipped_orphaned"] == 1
    assert store.load_run("gr_valid") is not None
    assert store.get_goal_run_id("real-session") == "gr_valid"
    assert store.load_run("gr_orphan") is None
    assert store.get_goal_run_id("fixture-session") == ""


def test_normal_goal_rows_are_not_imported(tmp_path):
    source = tmp_path / "legacy.db"
    _make_legacy_db(
        source,
        {
            "goal_run:normal-run": {
                "goal": "ordinary goal",
                "goal_run_id": "normal-run",
                "mode": "goal",
                "status": "active",
            },
            "goal:normal-session": {
                "goal": "ordinary mirror",
                "mode": "goal",
                "status": "paused",
            },
            "goal_session:normal-session": "normal-run",
            "goal_events:normal-run": [{"type": "set", "ts": 1}],
        },
    )
    store = SupergoalStore(db_path=tmp_path / "plugin.db")

    report = migrate_legacy_state(source, store=store, backup=False)

    assert report["status"] == "migrated"
    assert report["runs_imported"] == 0
    assert report["bindings_imported"] == 0
    assert report["events_imported"] == 0
    assert store.load_run("normal-run") is None
    assert store.get_goal_run_id("normal-session") == ""


def test_readonly_source_sees_committed_wal_rows(tmp_path):
    source = tmp_path / "legacy-wal.db"
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT)")
        writer.execute(
            "INSERT INTO state_meta(key, value) VALUES(?, ?)",
            (
                "goal_run:gr_wal",
                json.dumps(
                    {
                        "goal": "visible in wal",
                        "goal_run_id": "gr_wal",
                        "mode": "supergoal",
                        "status": "active",
                    }
                ),
            ),
        )
        writer.commit()

        store = SupergoalStore(db_path=tmp_path / "plugin.db")
        report = migrate_legacy_state(source, store=store, backup=False)

        assert report["runs_imported"] == 1
        assert store.load_run("gr_wal")["goal"] == "visible in wal"
    finally:
        writer.close()
