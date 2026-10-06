# Supergoal Runtime for Hermes Agent

[![CI](https://github.com/0error0warning/supergoal-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/0error0warning/supergoal-runtime/actions/workflows/ci.yml)

Standalone Hermes plugin for durable continuation of user-defined tasks.

> **Development snapshot, 2026-10-06.** This branch builds on the deployed 1.1.1
> source. It contains task-defined acceptance and an opt-in v2 engine. These
> changes have not been deployed; publishing this branch does not upgrade a
> production Hermes installation.

The runtime preserves task state, pending work and budgets, and records the
evidence used to continue, pause or finish. Domain workflows come from the user
and task contract. v1 remains the default; the experimental v2 plugin adds
versioned acceptance checks, calibration, disputes and durable dispatch.

## Research status

The earlier October 4–6 studies are complete and archived. The largest
public comparison has 22 evaluable paired tasks: full SG passed 15/22, native
Hermes 13/22 and repeated-goal 17/22. Full SG used 504 physical requests versus
323 for native Hermes. This sample does not establish a stable quality benefit.

In the common-prefix multi-stage study, all 18 online passes still had strict
test failures. The operational repairs have concrete regression evidence, while
reliable semantic acceptance and day-long autonomy remain unproven. The research
model reviewer and the opt-in plugin's configured checkers are separate paths.

The additional October 6 intent/evidence study is complete: 32 trials across six
new terminal tasks and two research tasks. Native Hermes passed 6/6 terminal
tasks; repeated-goal, original SG and the new policy each passed 5/6. The new
policy used 138 requests versus native's 89. Its one raw failure involved timing
tests; all four final artifacts passed five later serial timing runs each. The
original scores remain unchanged. Seven of eight research content grades failed
quote validation and remain ungraded. No quality advantage is established.

The GCP instance and experimental model tunnel are stopped, with persistent
disks retained. Production Hermes continues on its existing deployment. New v2
guidance is opt-in; the full brief/executor/model-review pipeline is experimental.

- [Research index: conclusions, studies, code and evidence](docs/research-index.md)
- [Earlier study findings and limitations](docs/research-findings-2026-10-06.md)
- [Project value and scope reassessment](docs/project-direction-2026-10-06.md)
- [Task-defined acceptance and compatibility](docs/generic-acceptance.md)
- [Opt-in v2 operator guide](docs/v2-plugin-and-acceptance.md)
- [Intent, research decisions and evidence policy](docs/intent-and-evidence-policy.md)
- [Intent/evidence results, failures and validation](docs/intent-transfer01-study.md)
- [Frozen intent/evidence transfer protocol](experiments/public_benchmarks/INTENT-TRANSFER01.md)
- [Research tools and reproduction scope](experiments/README.md)

## Architecture

```text
Hermes generic plugin ABI
  ├─ context-aware slash commands + busy-safe controls
  ├─ pre_tool_call / post_tool_call hooks
  ├─ post_llm_call + compression lineage
  └─ post-turn TurnDirective controller
                 │
                 ▼
       supergoal-runtime plugin
  ├─ RuntimeManager + deterministic gates
  ├─ policy guard + evidence ledger
  ├─ completion judge / advisory critic adapters
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
- Compression continuity uses Hermes' fork-aware lineage, so explicit branches,
  delegates, and tool sessions do not inherit a mission accidentally.
- Reconciliation is idempotent and fail-open, with a bounded five-minute miss
  cache to keep ordinary non-Supergoal turns cheap.
- Profile-scoped SQLite at `${HERMES_HOME}/supergoal/state.db`.
- WAL, foreign keys, explicit transactions, schema migrations, and idempotent tool-event writes.
- Acceptance comes from the user's goal, added criteria, and explicit task contract.
- Task keywords never select a built-in domain workflow. External sources and artifacts are optional unless requested.
- Explicit evidence requirements use actual tool/human records; assistant and critic claims cannot grant proof.
- Critic suggestions cannot rewrite the task contract or success definition.
- `pre_tool_call` enforces the mission permission contract in supervised mode.
- `post_tool_call` records redacted, session-scoped evidence and fails open for tool execution.
- Host hardline safety and explicit approval requirements always remain authoritative.
- User messages preempt and pause automatic continuation.
- PID wait barriers do not burn turns and automatically release when the process exits.
- Restart recovery uses only the plugin database.

## Hermes compatibility

The plugin requires a Hermes host exposing the following generic capabilities. A version number alone does not establish compatibility; the audited Tencent host is a customized v0.21.3:

- context-aware commands and native follow-up enqueue;
- post-turn `TurnDirective` controllers;
- busy-safe control subcommands;
- the supported `post_llm_call` hook;
- `SessionDB.get_compression_lineage()` for fork-aware continuity.

`post_llm_call` runs after a completed, non-interrupted turn and before the
post-turn controller. In-place compression is a no-op because the physical
session ID is already bound. When compression creates a child session, the
plugin walks only that child's compression ancestors and moves the current
binding before continuation is evaluated. Missing lineage or a transient host
read failure never breaks the Hermes response; transient failures are not
cached, so the next completed turn can retry immediately.

The plugin does not require Supergoal-specific product branches in Hermes Core. The audited production host carries generic turn-control extensions that the official versions checked in the audit do not expose. Direct host imports are the generic `TurnDirective` type and a narrow ordinary `/goal` conflict adapter.

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

## Upgrade and maintenance

Directory installation:

```bash
cd "$HERMES_HOME/plugins/supergoal-runtime"
git pull --ff-only
# Restart Hermes/Gateway after the pull.
```

Plugin releases normally update independently of Hermes Core. Before upgrading Hermes itself, run this repository's full test suite against the target Hermes checkout. While upstream PR #63208 remains unmerged, carry or reapply only the generic ABI commits; do not restore the retired Supergoal Core overlay.

Rollback remains straightforward: disable the plugin, restore `${HERMES_HOME}/supergoal/state.db` from backup if needed, and return Hermes to the previous known-good Core commit. The legacy importer never deletes old Core keys automatically.

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
