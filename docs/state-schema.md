# Plugin State Schema

Database: `${HERMES_HOME}/supergoal/state.db`

Current schema version: `2`.

## Connection contract

Every plugin-owned connection enables:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;
```

Writes use an explicit `BEGIN IMMEDIATE` / `COMMIT` boundary and roll back on any exception. The plugin never stores state in its source directory or Hermes `state_meta` after migration.

## Tables

### `schema_meta`

| column | purpose |
|---|---|
| `key` | metadata/migration key |
| `value` | string value |

`schema_version` records the current plugin schema. Legacy-import markers are stored as `legacy_migration:<source-path-hash>`.

### `runs`

One row per logical mission.

| column | purpose |
|---|---|
| `goal_run_id` | stable logical mission identity |
| `state_json` | canonical UTF-8 JSON object |
| `status` | indexed/inspectable status mirror |
| `state_schema_version` | product-state schema version |
| `legacy_source_key` | hashed source reference when imported |
| `created_at`, `updated_at` | Unix timestamps |

### `session_bindings`

Maps Hermes physical sessions to a logical run.

| column | purpose |
|---|---|
| `session_id` | unique physical session id |
| `goal_run_id` | referenced logical run |
| `reason` | start/compression/import reason |
| `is_current` | whether this physical session may drive continuation |
| `bound_at` | Unix timestamp |

Compression preserves the old binding for audit, marks it non-current, and inserts the new current binding in one transaction.

### `events`

Append-only mission event ledger.

| column | purpose |
|---|---|
| `id` | local sequence id |
| `goal_run_id` | owning logical run |
| `event_type` | typed event name |
| `event_json` | canonical event JSON |
| `observed_at` | event timestamp |
| `legacy_source_key/index` | idempotent import identity |

The `(goal_run_id, legacy_source_key, legacy_source_index)` uniqueness constraint prevents duplicate legacy events during forced/retried imports.

## Atomicity

- Normal controller writes use `save_run_with_events()` so state changes and emitted events commit together.
- Legacy migration uses `import_run_bundle()` so each run, all of its bindings, and all of its events commit or roll back together.
- Binding conflicts fail closed and never silently move a physical session to another run.

## Import-time safety

Importing `supergoal_runtime`, the root directory plugin, or running Hermes plugin discovery does not create the database. The database is initialized only on the first store operation.
