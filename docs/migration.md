# Legacy State Migration

The Phase 2 importer moves legacy Supergoal state from Hermes `state.db` into the plugin-owned database without deleting or mutating the source.

## Inputs

Recognized `state_meta` keys:

```text
goal_run:<goal_run_id>
goal_session:<session_id>
goal_events:<goal_run_id-or-session_id>
goal:<session_id>
```

Only states whose `mode` is `supergoal` are imported. Ordinary Hermes `/goal` remains owned by Hermes core.

## Identity rules

1. `goal_run:<id>` is authoritative when present.
2. `goal:<session>` compatibility mirrors fill gaps but do not override an authoritative run row.
3. Existing `goal_run_id` values are preserved.
4. Pre-`goal_run_id` rows receive a deterministic `legacy-<safe-session>-<hash>` id.
5. Explicit `goal_session` bindings override inferred compatibility bindings when the referenced run is imported.
6. Event keys are resolved through the run id or session binding and duplicate event payloads are collapsed before import.

## Safety rules

- Source SQLite is opened with `mode=ro` and `PRAGMA query_only=ON`; it does not use `immutable=1`, so committed WAL rows remain visible.
- Existing target DBs are backed up using SQLite's online backup API before writes.
- The old Hermes keys remain untouched.
- Malformed values are reported by hashed key reference and error code; raw keys/values are never included in default reports.
- Binding conflicts fail closed for that run.
- A source-path marker makes repeated **successful** execution return `already_migrated`.
- Partial/error migrations do not write the success marker; normal reruns retry unresolved runs.
- `--force` retries safely; existing plugin-owned runs are never overwritten, and database uniqueness constraints prevent duplicate imported events.

## Commands

```bash
PYTHONPATH=. python scripts/migrate_legacy_state.py --dry-run
PYTHONPATH=. python scripts/migrate_legacy_state.py
```

Options:

```text
--source PATH
--target PATH
--dry-run
--force
--no-backup
--show-paths
```

The command prints one JSON report and exits non-zero when row-level errors were encountered. Reports use hashed source/target/run references by default; `--show-paths` explicitly includes absolute paths for recovery work.

## Recovery

If validation fails after migration:

1. Disable the plugin.
2. Keep the new plugin DB for analysis.
3. Restore the target backup if needed.
4. Continue using the untouched Hermes legacy state on the rollback branch.

The migration script never performs legacy cleanup. Any future deletion of old keys requires a separate, explicitly confirmed operation after two stable plugin releases and a rollback drill.
