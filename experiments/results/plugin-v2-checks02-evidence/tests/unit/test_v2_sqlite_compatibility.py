"""A deployed vulnerable SQLite must never silently enable WAL."""
import sqlite3

import pytest

from supergoal_runtime.v2.kernel import Kernel


@pytest.mark.parametrize("version,mode", [
    ((3, 49, 1), "DELETE"), ((3, 51, 2), "DELETE"),
    ((3, 50, 6), "DELETE"), ((3, 44, 5), "DELETE"),
    ((3, 51, 3), "WAL"), ((3, 52, 0), "WAL"),
    ((3, 50, 7), "WAL"), ((3, 44, 6), "WAL"),
    ((3, 45, 0), "DELETE"),
])
def test_affected_sqlite_versions_keep_transactional_rollback_journal(tmp_path, monkeypatch, version, mode):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version)
    kernel = Kernel(tmp_path / "state.db")
    kernel.create("goal", {"outcome": "Deliver"})
    lease = kernel.claim("goal", "worker")
    kernel.finish(lease, {"verdict": "pass"})
    assert Kernel(kernel.path).state("goal")["status"] == "succeeded"
    with sqlite3.connect(kernel.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].upper() == mode
