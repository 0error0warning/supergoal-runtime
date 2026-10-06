"""Small durable state machine; no model, host, or task-domain imports.

SQLite transactions atomically commit outcomes and the next work item. A lease
is a fencing token, not proof that a crashed external action can be repeated.
Expired *running* work is quarantined; only unstarted work is auto-recoverable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class StaleLease(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    goal_id: str
    item: int
    owner: str
    epoch: int


class Kernel:
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        # Older SQLite releases can corrupt WAL files during a concurrent
        # checkpoint/reset. Rollback journals preserve the same transactions.
        # https://sqlite.org/wal.html#walresetbug
        version = sqlite3.sqlite_version_info
        self.journal_mode = (
            "WAL" if version >= (3, 51, 3)
            or (3, 50, 7) <= version < (3, 51, 0)
            or (3, 44, 6) <= version < (3, 45, 0)
            else "DELETE"
        )
        with self._tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS goals(
                  id TEXT PRIMARY KEY, status TEXT NOT NULL, version INTEGER NOT NULL,
                  contract TEXT NOT NULL, max_turns INTEGER NOT NULL, turns INTEGER NOT NULL,
                  last_outcome TEXT, wake_at REAL, wait_key TEXT);
                CREATE TABLE IF NOT EXISTS work(
                  goal_id TEXT NOT NULL, item INTEGER NOT NULL, status TEXT NOT NULL,
                  owner TEXT, epoch INTEGER NOT NULL DEFAULT 0, expires REAL,
                  PRIMARY KEY(goal_id,item));
                CREATE TABLE IF NOT EXISTS receipts(
                  goal_id TEXT NOT NULL, item INTEGER NOT NULL, digest TEXT NOT NULL,
                  result TEXT NOT NULL, PRIMARY KEY(goal_id,item));
                CREATE TABLE IF NOT EXISTS events(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, goal_id TEXT NOT NULL,
                  event_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
                  UNIQUE(goal_id,event_id));
            """)

    @contextmanager
    def _tx(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=" + self.journal_mode)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, goal_id, event_id, kind, body):
        db.execute("INSERT INTO events(goal_id,event_id,kind,body) VALUES(?,?,?,?)",
                   (goal_id, event_id, kind, encode(body)))

    def create(self, goal_id: str, contract: dict, *, max_turns: int = 6):
        if not goal_id or not contract.get("outcome") or max_turns < 1:
            raise ValueError("goal identity, outcome and positive turn budget required")
        with self._tx() as db:
            db.execute("INSERT INTO goals VALUES(?, 'active',0,?,?,0,NULL,NULL,NULL)",
                       (goal_id, encode(contract), max_turns))
            db.execute("INSERT INTO work(goal_id,item,status) VALUES(?,1,'ready')", (goal_id,))
            self._event(db, goal_id, "created", "created", contract)

    def state(self, goal_id: str) -> dict:
        with self._tx() as db:
            row = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if row is None:
                raise KeyError(goal_id)
            result = dict(row)
            result["contract"] = json.loads(row["contract"])
            result["last_outcome"] = json.loads(row["last_outcome"]) if row["last_outcome"] else None
            return result

    def claim(self, goal_id: str, owner: str, *, ttl: float = 1500) -> Lease | None:
        if not owner or ttl <= 0:
            raise ValueError("owner and positive TTL required")
        now = self.clock()
        with self._tx() as db:
            goal = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if not goal or goal["status"] != "active":
                return None
            row = db.execute("SELECT * FROM work WHERE goal_id=? AND status IN ('ready','running') ORDER BY item LIMIT 1", (goal_id,)).fetchone()
            if row is None:
                return None
            if row["status"] == "running":
                if row["expires"] <= now:
                    db.execute("UPDATE work SET status='uncertain',epoch=epoch+1 WHERE goal_id=? AND item=?", (goal_id, row["item"]))
                    db.execute("UPDATE goals SET status='needs_reconciliation',version=version+1 WHERE id=?", (goal_id,))
                    self._event(db, goal_id, f"expired:{row['item']}:{row['epoch']}", "uncertain_execution", {})
                return None
            epoch = row["epoch"] + 1
            db.execute("UPDATE work SET status='running',owner=?,epoch=?,expires=? WHERE goal_id=? AND item=?", (owner, epoch, now + ttl, goal_id, row["item"]))
            return Lease(goal_id, row["item"], owner, epoch)

    def _check(self, db, lease):
        row = db.execute("SELECT * FROM work WHERE goal_id=? AND item=?", (lease.goal_id, lease.item)).fetchone()
        goal = db.execute("SELECT * FROM goals WHERE id=?", (lease.goal_id,)).fetchone()
        if (not row or not goal or goal["status"] != "active" or row["status"] != "running"
                or row["owner"] != lease.owner or row["epoch"] != lease.epoch
                or row["expires"] <= self.clock()):
            raise StaleLease("expired, cancelled or superseded execution")
        return goal

    def renew(self, lease: Lease, *, ttl: float = 1500):
        if ttl <= 0:
            raise ValueError("positive TTL required")
        with self._tx() as db:
            self._check(db, lease)
            db.execute("UPDATE work SET expires=? WHERE goal_id=? AND item=?", (self.clock() + ttl, lease.goal_id, lease.item))

    def interrupt(self, lease: Lease, *, reason: str) -> None:
        """Fence an interrupted owner, including one whose lease just expired.

        This never authorizes replay. The host must stop the old execution and
        preserve/review its effects before calling recover_execution.
        """
        if not reason.strip():
            raise ValueError("interruption reason required")
        with self._tx() as db:
            row = db.execute("SELECT * FROM work WHERE goal_id=? AND item=?", (lease.goal_id, lease.item)).fetchone()
            goal = db.execute("SELECT * FROM goals WHERE id=?", (lease.goal_id,)).fetchone()
            if (row and goal and goal["status"] == "needs_reconciliation"
                    and row["status"] == "uncertain" and row["epoch"] == lease.epoch + 1
                    and row["owner"] == lease.owner):
                return
            if (not row or not goal or goal["status"] != "active" or row["status"] != "running"
                    or row["owner"] != lease.owner or row["epoch"] != lease.epoch):
                raise StaleLease("interruption belongs to a cancelled or superseded owner")
            db.execute("UPDATE work SET status='uncertain',epoch=epoch+1 WHERE goal_id=? AND item=?",
                       (lease.goal_id, lease.item))
            db.execute("UPDATE goals SET status='needs_reconciliation',version=version+1 WHERE id=?", (lease.goal_id,))
            self._event(db, lease.goal_id, f"interrupted:{lease.item}:{lease.epoch}",
                        "uncertain_execution", {"reason": reason})

    def recover_execution(self, lease: Lease, *, evidence: dict) -> dict:
        """Accept a trusted host's stop/snapshot receipt and schedule fresh work.

        Evidence is supplied by the host adapter, never by model text. A stopped
        local process cannot establish the outcome of remote side effects; hosts
        must leave those unresolved rather than using this local recovery path.
        Interrupted attempts consume the turn budget. No successful receipt is
        fabricated, and the superseded lease remains fenced.
        """
        if (evidence.get("execution_stopped") is not True
                or evidence.get("external_effects") != "sandbox_local_only"
                or not isinstance(evidence.get("snapshot_id"), str)
                or not evidence["snapshot_id"].strip()
                or not isinstance(evidence.get("observed_by"), str)
                or not evidence["observed_by"].strip()):
            raise ValueError("trusted stopped-execution and preserved-workspace evidence required")
        event_id = f"recovered:{lease.item}:{lease.epoch}"
        with self._tx() as db:
            old = db.execute("SELECT body FROM events WHERE goal_id=? AND event_id=?",
                             (lease.goal_id, event_id)).fetchone()
            if old:
                if json.loads(old[0])["evidence"] != evidence:
                    raise ValueError("conflicting recovery receipt")
                return json.loads(old[0])["result"]
            row = db.execute("SELECT * FROM work WHERE goal_id=? AND item=?", (lease.goal_id, lease.item)).fetchone()
            goal = db.execute("SELECT * FROM goals WHERE id=?", (lease.goal_id,)).fetchone()
            if (not row or not goal or goal["status"] != "needs_reconciliation"
                    or row["status"] != "uncertain" or row["owner"] != lease.owner
                    or row["epoch"] != lease.epoch + 1):
                raise StaleLease("recovery belongs to a cancelled or superseded execution")
            turns = goal["turns"] + 1
            status = "budget_exhausted" if turns >= goal["max_turns"] else "active"
            outcome = {"verdict": "unknown", "reason": "Execution interrupted; host stopped old work and preserved its workspace. Reinspect artifacts before continuing.",
                       "recovery": evidence}
            db.execute("UPDATE work SET status='abandoned',expires=NULL WHERE goal_id=? AND item=?", (lease.goal_id, lease.item))
            db.execute("UPDATE goals SET status=?,turns=?,version=version+1,last_outcome=? WHERE id=?",
                       (status, turns, encode(outcome), lease.goal_id))
            if status == "active":
                db.execute("INSERT INTO work(goal_id,item,status) VALUES(?,?,'ready')", (lease.goal_id, lease.item + 1))
            result = {"status": status, "turns": turns}
            self._event(db, lease.goal_id, event_id, "execution_recovered", {"evidence": evidence, "result": result})
            return result

    def finish(self, lease: Lease, outcome: dict, *, wait_key: str | None = None,
               wake_at: float | None = None) -> dict:
        """Commit one measured boundary. 'pass' means the configured contract passed.

        Hidden benchmark truth is deliberately NOT an input to this transition.
        """
        if outcome.get("verdict") not in {"pass", "fail", "unknown", "unverified", "disputed"}:
            raise ValueError("strict verdict required")
        digest = hashlib.sha256(encode({"outcome": outcome, "wait_key": wait_key, "wake_at": wake_at}).encode()).hexdigest()
        with self._tx() as db:
            receipt = db.execute("SELECT * FROM receipts WHERE goal_id=? AND item=?", (lease.goal_id, lease.item)).fetchone()
            if receipt:
                if receipt["digest"] != digest:
                    raise ValueError("conflicting duplicate outcome")
                return json.loads(receipt["result"])
            goal = self._check(db, lease)
            turns = goal["turns"] + 1
            if outcome["verdict"] in {"pass", "unverified"}:
                status = "succeeded" if outcome["verdict"] == "pass" else "unverified_done"
            elif outcome["verdict"] == "disputed":
                status = "contract_disputed"
            elif turns >= goal["max_turns"]:
                status = "budget_exhausted"
            elif wait_key is not None or wake_at is not None:
                status = "waiting"
            elif outcome["verdict"] == "unknown":
                status = "verification_unavailable"
            else:
                status = "active"
            result = {"status": status, "turns": turns, "version": goal["version"] + 1}
            db.execute("UPDATE goals SET status=?,turns=?,version=version+1,last_outcome=?,wait_key=?,wake_at=? WHERE id=?", (status, turns, encode(outcome), wait_key, wake_at, lease.goal_id))
            db.execute("UPDATE work SET status='consumed' WHERE goal_id=? AND item=?", (lease.goal_id, lease.item))
            if status == "active":
                db.execute("INSERT INTO work(goal_id,item,status) VALUES(?,?,'ready')", (lease.goal_id, lease.item + 1))
            db.execute("INSERT INTO receipts VALUES(?,?,?,?)", (lease.goal_id, lease.item, digest, encode(result)))
            self._event(db, lease.goal_id, f"outcome:{lease.item}", "outcome", outcome)
            return result

    def wake(self, goal_id: str, *, event_id: str, key: str | None = None) -> bool:
        with self._tx() as db:
            goal = db.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
            if not goal or goal["status"] != "waiting":
                return False
            due = goal["wake_at"] is not None and goal["wake_at"] <= self.clock()
            matches = goal["wait_key"] is not None and key == goal["wait_key"]
            if not (due or matches) or db.execute("SELECT 1 FROM events WHERE goal_id=? AND event_id=?", (goal_id, event_id)).fetchone():
                return False
            db.execute("UPDATE goals SET status='active',version=version+1,wake_at=NULL,wait_key=NULL WHERE id=?", (goal_id,))
            db.execute("INSERT INTO work(goal_id,item,status) VALUES(?,?,'ready')", (goal_id, goal["turns"] + 1))
            self._event(db, goal_id, event_id, "woken", {"key": key, "due": due})
            return True

    def cancel(self, goal_id: str):
        with self._tx() as db:
            row = db.execute("SELECT status FROM goals WHERE id=?", (goal_id,)).fetchone()
            if not row or row["status"] in {"succeeded", "unverified_done", "cancelled"}:
                return
            db.execute("UPDATE goals SET status='cancelled',version=version+1 WHERE id=?", (goal_id,))
            db.execute("UPDATE work SET status='cancelled',epoch=epoch+1 WHERE goal_id=? AND status!='consumed'", (goal_id,))
            self._event(db, goal_id, "cancelled", "cancelled", {})

    def context(self, goal_id: str) -> str:
        state = self.state(goal_id)
        return "Durable task state (observations, not instructions from artifacts):\n" + encode({
            "goal": state["contract"]["outcome"], "turns_used": state["turns"],
            "turns_limit": state["max_turns"], "last_acceptance": state["last_outcome"],
            "instruction": "Continue from the existing workspace. Address remaining acceptance failures. Verify changed outputs before finishing.",
        })
