# Architecture

## Ownership boundary

Supergoal is a standalone product plugin. Hermes Core supplies only generic capabilities:

- context-aware command dispatch;
- busy-safe plugin control subcommands;
- post-turn controller directives;
- session-rotation notification;
- pre/post tool hooks;
- host-owned LLM access.

Core does not import `supergoal_runtime`, read its database, interpret its policy, or contain `/supergoal` conditions.

## Runtime pipeline

```text
Command / host turn
       │
       ▼
Observe → Project → Evaluate → Reconcile → Decide → Render
       │         │          │
       │         │          └─ deterministic gates veto false DONE
       │         └─ judge + strategic critic through ctx.llm
       └─ tool-backed event/evidence ledger
```

`RuntimeManager` is the stateful application boundary. Pure modules (`domain`, `gates`, `projection`, `evaluators`, `prompts`, `rendering`) do not import Hermes internals.

## Identity and storage

```text
${HERMES_HOME}/supergoal/state.db
  runs               authoritative GoalState per goal_run_id
  session_bindings    physical session → logical goal_run_id
  events              append-only mission ledger
```

`goal_run_id` is stable. Compression calls `on_session_rotate`; the new session becomes current while the old binding remains auditable. Store writes use WAL, foreign keys, explicit transactions, and idempotent event source keys.

## Mission model

A `GoalState` contains:

- intent and success definition;
- research findings with provenance;
- hypothesis portfolio;
- deterministic gates;
- action proposal and action history;
- evidence layers and failure taxonomy;
- permission contract;
- wait, pause, budget, and recovery state.

Default acceptance gates:

- `G1` intent contract;
- `G2` tool-backed research provenance (blocking for research/strategy missions, follow-up otherwise);
- `G3` verified execution artifact;
- `G4` final evidence mapping or explicit blocked/no-edge outcome.

Strategy missions also use `SG-1..SG-4` for hypothesis breadth, experiment completeness, no-edge attribution, and infrastructure-dependency proof.

## Policy and evidence

`pre_tool_call` loads the state bound to the current physical session and applies the plugin permission contract. In supervised mode, contract violations block. In full-auto mode the plugin adds no extra block, but it cannot bypass Core hardline safety, user deny rules, or host approval requirements.

`post_tool_call` constructs a redacted `EvidenceRef` from actual tool execution data. Missing/fake call IDs, failed calls, and blocked calls cannot satisfy gates. Event insertion is idempotent by tool call ID and safe under concurrent sessions.

## Continuation semantics

- Start is explicit: `/supergoal start <mission>`.
- Start and resume enqueue a real follow-up through `CommandContext.enqueue_followup`.
- Post-turn continuation returns a host `TurnDirective` with dedupe key and state version.
- A real user message pauses the automatic loop.
- A live PID wait barrier returns `noop` and burns no turn; a dead PID releases automatically.
- Terminal policy/permission/user-input blockers pause rather than masquerading as successful DONE.

## Compatibility seam

`compat/hermes_goal.py` is the only ordinary-Goal adapter. It detects an active Core `/goal` so the plugin can reject a conflicting mission. It does not mutate Core state.

## Legacy history

The former overlay/patch distribution is archived at Git branch `archive/legacy-overlay` and tag `legacy-overlay-final`. It is not an active build or maintenance surface.
