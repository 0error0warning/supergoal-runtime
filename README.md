# Supergoal Runtime for Hermes Agent

Standalone Hermes plugin for long-running, evidence-first autonomous missions.

> Status: **Phase 5 implementation complete**. The plugin owns Supergoal state, policy, evidence, controller, commands, replay tests, and compression continuity. Production cutover is governed by [`docs/repository-cleanup-and-plugin-migration-spec.md`](docs/repository-cleanup-and-plugin-migration-spec.md).

## Architecture

```text
Hermes generic plugin ABI
  ├─ context-aware slash commands + busy-safe controls
  ├─ pre_tool_call / post_tool_call hooks
  ├─ post-turn TurnDirective controller
  └─ on_session_rotate hook
                 │
                 ▼
       supergoal-runtime plugin
  ├─ RuntimeManager + deterministic gates
  ├─ policy guard + evidence ledger
  ├─ judge / strategic critic adapters
  └─ ${HERMES_HOME}/supergoal/state.db
```

Hermes Core remains product-name-agnostic. This repository is the only active source of Supergoal product code.

## Commands

The plugin registers:

- `/supergoal start <mission>` — explicit mission start and real kickoff turn
- `/supergoal status`
- `/supergoal pause`
- `/supergoal resume` — immediately queues a real continuation turn
- `/supergoal clear`
- `/supergoal wait <pid>` / `/supergoal unwait`
- `/supergoal replan`
- aliases: `/sgoal`, `/sgx`

Plain `/supergoal <text>` does **not** start a mission. Start is explicit to prevent accidental long-running loops.

## Runtime guarantees

- Stable logical `goal_run_id` across physical session rotation/compression.
- Profile-scoped SQLite at `${HERMES_HOME}/supergoal/state.db`.
- WAL, foreign keys, explicit transactions, schema migrations, and idempotent tool-event writes.
- Deterministic acceptance gates, strategy gates, action taxonomy, and inertia guard.
- Tool-backed evidence only; assistant prose cannot satisfy execution/research gates.
- `pre_tool_call` enforces the mission permission contract in supervised mode.
- `post_tool_call` records redacted, session-scoped evidence and fails open for tool execution.
- Host hardline safety and explicit approval requirements always remain authoritative.
- User messages preempt and pause automatic continuation.
- PID wait barriers do not burn turns and automatically release when the process exits.
- Restart recovery uses only the plugin database.

## Install

Directory plugin:

```bash
mkdir -p "$HERMES_HOME/plugins"
git clone https://github.com/0error0warning/supergoal-runtime.git \
  "$HERMES_HOME/plugins/supergoal-runtime"
hermes plugins enable supergoal-runtime --no-allow-tool-override
```

Python package:

```bash
python -m pip install git+https://github.com/0error0warning/supergoal-runtime.git
hermes plugins enable supergoal-runtime --no-allow-tool-override
```

Restart Hermes after enabling the plugin. The plugin declares no model tools and requires no plugin-specific API key; judge/critic calls use the host-owned `ctx.llm` facade.

## Repository layout

```text
plugin.yaml
pyproject.toml
supergoal_runtime/
  command.py             # context-aware /supergoal command surface
  runtime.py             # stateful command + post-turn orchestration
  domain.py              # serializable mission model
  gates.py               # deterministic gates and inertia guard
  projection.py          # observation/event projection
  evaluators.py          # critic merge and evaluator adapters
  prompts.py             # judge/critic/continuation prompts
  rendering.py           # platform-neutral status rendering
  policy.py              # pre/post tool hooks
  evidence.py            # redacted EvidenceRef construction
  store.py               # plugin-owned SQLite
  migration.py           # read-only legacy importer
  compat/hermes_goal.py  # narrow ordinary /goal conflict adapter
scripts/
  migrate_legacy_state.py
tests/
  contract/
  integration/
  replay/
  unit/
```

The old Core overlay and generated patch are preserved only on Git branch `archive/legacy-overlay` and tag `legacy-overlay-final`; they are not present on the plugin mainline.

## Legacy migration

Dry-run first:

```bash
PYTHONPATH=. python scripts/migrate_legacy_state.py --dry-run
```

Import:

```bash
PYTHONPATH=. python scripts/migrate_legacy_state.py
```

The importer opens the legacy `${HERMES_HOME}/state.db` read-only, imports only `mode=supergoal`, preserves logical run IDs and bindings, retains old Core keys for rollback, and redacts malformed values from reports. See [`docs/migration.md`](docs/migration.md).

## Tests

Against a compatible Hermes checkout:

```bash
export PYTHONPATH=/path/to/supergoal-runtime:/path/to/hermes-agent
/path/to/hermes-venv/bin/python -m pytest tests -q -o 'addopts='
/path/to/hermes-venv/bin/python -m ruff check supergoal_runtime scripts tests
python -m compileall -q supergoal_runtime scripts tests
```

Coverage includes:

- real Bitget, completion-conflict, and compression JSONL replay traces;
- command semantics and real enqueue on start/resume;
- ordinary `/goal` conflict and per-session isolation;
- wait, terminal blocker, user preemption, restart recovery, and compression;
- policy deny/full-auto interaction with host safety;
- concurrent evidence writes, idempotence, blocked evidence, fake call IDs, and secret redaction;
- legacy migration, backup, idempotence, profile isolation, and schema contracts.

## License

See [LICENSE](LICENSE).
