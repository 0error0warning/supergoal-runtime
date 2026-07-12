# Supergoal Runtime for Hermes Agent

Standalone Hermes plugin for long-running, evidence-first mission control.

> Migration status: **Phase 2 — formal plugin skeleton and profile-scoped SQLite storage**. The legacy overlay remains frozen migration input and is not the active distribution model.

## Current architecture

```text
Hermes generic plugin ABI
  ├─ context-aware slash commands
  ├─ post-turn controller directives
  └─ session rotation hook
              ↑
      supergoal-runtime plugin
              └─ ${HERMES_HOME}/supergoal/state.db
```

The plugin repository is the future single source of truth for Supergoal product code. Hermes core must remain name-agnostic and must not import this package.

## Phase 2 capabilities

- Formal directory plugin: `plugin.yaml` + root `__init__.py` + `register(ctx)`.
- Pip metadata and `hermes_agent.plugins` entry point in `pyproject.toml`.
- No model tools, secrets, import-time database opens, threads, or background tasks.
- Lazy profile-scoped database at `${HERMES_HOME}/supergoal/state.db`.
- SQLite WAL, foreign keys, explicit write transactions, schema versioning, and idempotent schema migration.
- Separate `runs`, `session_bindings`, and append-only `events` tables.
- Atomic run+event writes and atomic legacy run/binding/event imports.
- Read-only, idempotent legacy importer for Hermes `state_meta` keys.
- Compression rotation keeps one logical `goal_run_id`, preserves old bindings for audit, and marks only the new physical session current.

The temporary `/sgx start` command remains only as the Phase 1 host-ABI consumer. Full `/supergoal` commands and the migrated controller arrive in later phases.

## Repository layout

```text
plugin.yaml
pyproject.toml
__init__.py
supergoal_runtime/
  __init__.py
  plugin.py
  config.py
  store.py
  migration.py
scripts/
  migrate_legacy_state.py
tests/
  contract/
  integration/
  unit/
  test_phase1_plugin_contract.py
docs/
  repository-cleanup-and-plugin-migration-spec.md
  phase1-verdict.md
  state-schema.md
  migration.md
```

`overlay/`, `patches/`, and `scripts/apply.sh` are frozen legacy artifacts. They are retained until the production gates in the migration specification pass; do not regenerate or extend them.

## Install as a directory plugin

```bash
mkdir -p "$HERMES_HOME/plugins"
ln -s /path/to/supergoal-runtime \
  "$HERMES_HOME/plugins/supergoal-runtime"
hermes plugins enable supergoal-runtime --no-allow-tool-override
```

Restart the Hermes process after enabling the plugin.

## Install as a Python package

```bash
python -m pip install /path/to/supergoal-runtime
hermes plugins enable supergoal-runtime --no-allow-tool-override
```

The package exposes the `hermes_agent.plugins` entry point. It does not require API keys or other secret environment variables.

## State location

```text
${HERMES_HOME}/supergoal/state.db
```

`HERMES_HOME` is resolved when a store instance is created, not when modules are imported. Therefore default and named Hermes profiles receive fully separate databases.

## Legacy migration

Dry-run first:

```bash
PYTHONPATH=. python scripts/migrate_legacy_state.py --dry-run
```

Import:

```bash
PYTHONPATH=. python scripts/migrate_legacy_state.py
```

Defaults:

- source: `${HERMES_HOME}/state.db` (opened read-only)
- target: `${HERMES_HOME}/supergoal/state.db`
- target backup: created when the target already exists
- old Hermes keys: retained
- repeated successful execution: returns `already_migrated` unless `--force` is used
- partial/error execution: no success marker is written, so unresolved runs are retried

The report is structured JSON and never includes raw malformed values. Source, target, run, and backup locations are hashed by default; pass `--show-paths` only when an operator needs exact recovery paths.

## Tests

With the Phase 1 Hermes ABI worktree available:

```bash
export PYTHONPATH=/path/to/supergoal-runtime:/path/to/hermes-agent
/path/to/hermes-venv/bin/python -m pytest tests -q -o 'addopts='
python -m compileall -q supergoal_runtime scripts tests
```

Phase 2 tests cover:

- real Hermes directory-plugin discovery and enable gating;
- side-effect-free plugin import/discovery;
- profile isolation;
- WAL/foreign-key/schema contracts;
- binding conflict fail-closed behavior;
- atomic state/event writes;
- compression binding rotation;
- read-only legacy migration, backup, idempotence, and error redaction.

## Migration phases

The authoritative plan is [`docs/repository-cleanup-and-plugin-migration-spec.md`](docs/repository-cleanup-and-plugin-migration-spec.md).

- Phase 0: audit and snapshot — complete
- Phase 1: generic Hermes host ABI spike — validated
- Phase 2: formal plugin skeleton and independent storage — current
- Phase 3+: domain/controller, policy/evidence hooks, command loop, production switch, cleanup

## License

See [LICENSE](LICENSE).
