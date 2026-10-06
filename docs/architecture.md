# Architecture

## Ownership boundary

Supergoal is a standalone product plugin. Hermes Core supplies only generic capabilities:

- context-aware command dispatch;
- busy-safe plugin control subcommands;
- post-turn controller directives;
- the `post_llm_call` lifecycle hook and compression lineage query;
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

The pipeline above describes the steps taken by the active `RuntimeManager.after_turn` path. The older `SupergoalController` is still present as a separate callback-based abstraction and is not the plugin entry point; unifying those paths remains follow-up work.

## Identity and storage

```text
${HERMES_HOME}/supergoal/state.db
  runs               authoritative GoalState per goal_run_id
  session_bindings    physical session → logical goal_run_id
  events              append-only mission ledger
```

`goal_run_id` is stable. After each completed turn, `post_llm_call` first checks
whether the physical session is already bound. For an unbound session it reads
Hermes' fork-aware compression lineage and searches backward for the current
Supergoal ancestor. A match atomically makes the compression child current;
the old binding remains auditable and pending continuation rows follow the new
session. Explicit branches, delegates, and tool sessions have no compression
ancestor and therefore do not inherit the mission.

The reconciliation path is idempotent and fail-open. Rotation accepts only an
ancestor still marked current, and the audit event has a deterministic source
key. A thread-safe, 1,024-entry cache keeps confirmed lineage misses for five
minutes, preventing repeated reads on ordinary sessions. Host/database errors
are not cached, so a later turn can retry immediately. Store writes use WAL,
foreign keys, explicit transactions, and idempotent event source keys.

## Mission model

A `GoalState` contains:

- the user goal, additional criteria, and explicit task contract;
- research findings with provenance;
- deterministic gates;
- action proposal and action history;
- recorded evidence layers;
- permission contract;
- wait, pause, budget, and recovery state.

Default acceptance gates:

- `G1` intent contract;
- `G2` optional tool-backed external provenance; it never adds a research quota;
- `G3` evidence types explicitly required by `GoalContract.evidence_requirements`, otherwise optional;
- `G4` the completion judge confirms the user's requested outcome.

Goal text does not select domain rules. The runtime has no built-in hypothesis count, baseline experiment, failure-attribution template, or preferred task method. Text deliverables can complete without file/tool evidence. When execution or external evidence is required, prose claims cannot replace actual records, and the judge must still evaluate their relevance to the requested outcome.

The critic updates advisory progress, plan health, and next steps. It cannot change the goal, success definition, contract, or grant proof. Completion requires a judge verdict plus any explicit evidence requirements; generic tool use and a sentence claiming completion do not override a continuing judge verdict.

Old `SG-1..SG-4` gates are moved to `retired_gates` on load/evaluation. Historical hypothesis/no-edge fields remain readable for migration, but are omitted from evaluator boards and no longer control completion or replanning. See [task-neutral acceptance](generic-acceptance.md).

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
