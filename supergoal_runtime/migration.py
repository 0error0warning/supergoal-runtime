"""One-way, idempotent import from Hermes' legacy ``state_meta`` rows."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from .store import (
    BindingConflictError,
    RunConflictError,
    SupergoalStore,
)


def _safe_ref(value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def _safe_error(key: str, code: str) -> dict[str, str]:
    return {"ref": _safe_ref(key), "error": code}


def _decode_object(
    key: str,
    raw: str,
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        errors.append(_safe_error(key, "invalid_json"))
        return None
    if not isinstance(value, dict):
        errors.append(_safe_error(key, "expected_object"))
        return None
    return value


def _decode_events(
    key: str,
    raw: str,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        errors.append(_safe_error(key, "invalid_json"))
        return []
    if not isinstance(value, list):
        errors.append(_safe_error(key, "expected_event_list"))
        return []
    events: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not str(item.get("type") or "").strip():
            errors.append(_safe_error(f"{key}[{index}]", "invalid_event"))
            continue
        events.append(dict(item))
    return events


def legacy_goal_run_id(session_id: str) -> str:
    """Create a deterministic, path-safe id for pre-goal_run_id states."""

    clean = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or ""))
    clean = clean.strip("._-") or "session"
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:10]
    return f"legacy-{clean[:80]}-{digest}"


def _marker_key(source: Path) -> str:
    digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:20]
    return f"legacy_migration:{digest}"


def _read_legacy_rows(source: Path) -> dict[str, str]:
    uri = f"file:{quote(str(source.resolve()), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='state_meta'"
        ).fetchone()
        if not table:
            raise ValueError("legacy database has no state_meta table")
        rows = conn.execute(
            """
            SELECT key, value FROM state_meta
            WHERE key LIKE 'goal_run:%'
               OR key LIKE 'goal_session:%'
               OR key LIKE 'goal_events:%'
               OR key LIKE 'goal:%'
            ORDER BY key
            """
        ).fetchall()
        return {str(key): str(value or "") for key, value in rows}
    finally:
        conn.close()


def _plan_import(rows: Mapping[str, str]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    runs: dict[str, dict[str, Any]] = {}
    run_source_keys: dict[str, str] = {}
    session_to_run: dict[str, str] = {}

    # Authoritative logical run rows win over compatibility goal:<session> mirrors.
    for key, raw in rows.items():
        if not key.startswith("goal_run:"):
            continue
        run_id = key.removeprefix("goal_run:").strip()
        state = _decode_object(key, raw, errors)
        if not run_id or state is None:
            continue
        if str(state.get("mode") or "goal") != "supergoal":
            continue
        stored_id = str(state.get("goal_run_id") or "").strip()
        if stored_id and stored_id != run_id:
            errors.append(_safe_error(key, "goal_run_id_mismatch"))
        state["goal_run_id"] = run_id
        runs[run_id] = state
        run_source_keys[run_id] = key

    for key, raw in rows.items():
        if not key.startswith("goal:"):
            continue
        session_id = key.removeprefix("goal:")
        state = _decode_object(key, raw, errors)
        if state is None or str(state.get("mode") or "goal") != "supergoal":
            continue
        run_id = str(state.get("goal_run_id") or "").strip()
        if not run_id:
            run_id = legacy_goal_run_id(session_id)
        state["goal_run_id"] = run_id
        session_to_run.setdefault(session_id, run_id)
        if run_id not in runs:
            runs[run_id] = state
            run_source_keys[run_id] = key

    # Explicit bindings override inferred compatibility bindings, but only when
    # their target is an imported Supergoal run.
    for key, raw in rows.items():
        if not key.startswith("goal_session:"):
            continue
        session_id = key.removeprefix("goal_session:")
        run_id = str(raw or "").strip()
        if run_id in runs:
            session_to_run[session_id] = run_id

    bindings_by_run: dict[str, list[tuple[str, str]]] = {
        run_id: [] for run_id in runs
    }
    for session_id, run_id in sorted(session_to_run.items()):
        if run_id in bindings_by_run:
            bindings_by_run[run_id].append((session_id, "legacy_import"))

    events_by_run: dict[str, list[tuple[dict[str, Any], str, int]]] = {
        run_id: [] for run_id in runs
    }
    event_fingerprints: dict[str, set[str]] = {run_id: set() for run_id in runs}
    for key, raw in rows.items():
        if not key.startswith("goal_events:"):
            continue
        suffix = key.removeprefix("goal_events:")
        run_id = suffix if suffix in runs else session_to_run.get(suffix, "")
        if run_id not in runs:
            continue
        for index, event in enumerate(_decode_events(key, raw, errors)):
            fingerprint = json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint in event_fingerprints[run_id]:
                continue
            event_fingerprints[run_id].add(fingerprint)
            events_by_run[run_id].append((event, _safe_ref(key), index))

    return {
        "runs": runs,
        "run_source_keys": run_source_keys,
        "bindings_by_run": bindings_by_run,
        "events_by_run": events_by_run,
        "errors": errors,
    }


def _base_report(
    *,
    status: str,
    source: Path,
    target_store: SupergoalStore,
    marker_key: str,
    errors: list[dict[str, str]] | None = None,
    include_paths: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": status,
        "source_ref": _safe_ref(str(source)),
        "target_ref": _safe_ref(str(target_store.db_path)),
        "runs_imported": 0,
        "bindings_imported": 0,
        "events_imported": 0,
        "run_results": [],
        "errors": list(errors or []),
        "backup_created": False,
        "backup_ref": None,
        "backup_name": None,
        "marker": marker_key,
    }
    if include_paths:
        report["source_path"] = str(source)
        report["target_path"] = str(target_store.db_path)
        report["backup_path"] = None
    return report


def migrate_legacy_state(
    source_db: str | Path,
    *,
    store: SupergoalStore | None = None,
    target_db: str | Path | None = None,
    backup: bool = True,
    dry_run: bool = False,
    force: bool = False,
    include_paths: bool = False,
) -> dict[str, Any]:
    """Import legacy Supergoal rows without deleting or modifying the source."""

    source = Path(source_db).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if store is not None and target_db is not None:
        raise ValueError("pass store or target_db, not both")
    target_store = store or SupergoalStore(db_path=target_db)
    target_existed = target_store.db_path.exists()
    marker_key = _marker_key(source)

    # Parse the read-only source first. A dry-run must not touch the target at all.
    rows = _read_legacy_rows(source)
    plan = _plan_import(rows)
    report = _base_report(
        status="dry_run" if dry_run else "migrated",
        source=source,
        target_store=target_store,
        marker_key=marker_key,
        errors=plan["errors"],
        include_paths=include_paths,
    )
    if dry_run:
        report["runs_discovered"] = len(plan["runs"])
        report["bindings_discovered"] = sum(
            len(items) for items in plan["bindings_by_run"].values()
        )
        report["events_discovered"] = sum(
            len(items) for items in plan["events_by_run"].values()
        )
        report["run_results"] = [
            {"run_ref": _safe_ref(run_id), "status": "planned"}
            for run_id in sorted(plan["runs"])
        ]
        return report

    # Read the marker without creating/upgrading the target DB.
    if not force and target_store.peek_meta(marker_key):
        return _base_report(
            status="already_migrated",
            source=source,
            target_store=target_store,
            marker_key=marker_key,
            include_paths=include_paths,
        )

    # The backup must precede ensure_schema(), marker writes, and all imports.
    if backup and target_existed:
        backup_path = target_store.backup()
        if backup_path:
            report["backup_created"] = True
            report["backup_ref"] = _safe_ref(str(backup_path))
            report["backup_name"] = backup_path.name
            if include_paths:
                report["backup_path"] = str(backup_path)

    for run_id in sorted(plan["runs"]):
        run_ref = _safe_ref(run_id)
        try:
            counts = target_store.import_run_bundle(
                run_id,
                plan["runs"][run_id],
                bindings=plan["bindings_by_run"].get(run_id, []),
                events=plan["events_by_run"].get(run_id, []),
                legacy_source_key=_safe_ref(
                    plan["run_source_keys"].get(run_id, run_id)
                ),
            )
        except RunConflictError:
            report["errors"].append(
                {"ref": run_ref, "error": "run_conflict"}
            )
            report["run_results"].append(
                {"run_ref": run_ref, "status": "run_conflict"}
            )
            continue
        except BindingConflictError:
            report["errors"].append(
                {"ref": run_ref, "error": "binding_conflict"}
            )
            report["run_results"].append(
                {"run_ref": run_ref, "status": "binding_conflict"}
            )
            continue

        report["runs_imported"] += counts["runs"]
        report["bindings_imported"] += counts["bindings"]
        report["events_imported"] += counts["events"]
        report["run_results"].append(
            {
                "run_ref": run_ref,
                "status": "imported" if counts["runs"] else "already_present",
                "runs_imported": counts["runs"],
                "bindings_imported": counts["bindings"],
                "events_imported": counts["events"],
            }
        )

    report["status"] = "migrated_with_errors" if report["errors"] else "migrated"
    if not report["errors"]:
        marker_value = json.dumps(
            {
                "status": report["status"],
                "runs_imported": report["runs_imported"],
                "bindings_imported": report["bindings_imported"],
                "events_imported": report["events_imported"],
                "run_count": len(report["run_results"]),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        target_store.set_meta(marker_key, marker_value)
    return report
