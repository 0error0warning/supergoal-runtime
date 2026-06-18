# Supergoal Runtime for Hermes Agent

A focused patch package for evolving Hermes Agent `/supergoal` from a high-budget continuation loop into a **Mission Control runtime** for ultra-long autonomous work.

This repository is intentionally an **overlay / patchset**, not a full fork of Hermes Agent. It contains the smallest reviewable set of runtime changes, tests, docs, and real reference-run logs needed to study and apply the `/supergoal` upgrade.

## Why build `/supergoal`?

Normal `/goal` is useful: keep a task active, judge after each turn, continue until done or paused. But it is still fundamentally a *standing-goal loop*.

`/supergoal` exists for a more ambitious operating mode:

> Give an agent a very high budget, very long runtime, durable mission state, permission boundaries, evidence ledgers, and self-correction machinery so it can work independently for hours or days without collapsing into drift, busywork, or narrative progress.

The blunt critique of the current generation is fair: early `/supergoal` can look like “a mode for automatically burning tokens when tokens are abundant.” That critique is exactly why this patch exists. A larger token budget alone does not create intelligence. It often just gives the agent more room to repeat itself, overbuild infrastructure, and confuse local progress with mission success.

The long-term target is different:

- **ultra-high budget** — token budget is high enough that the limiting factor becomes control quality, not context anxiety;
- **ultra-long runtime** — work can survive many turns, compression boundaries, and session resumes;
- **self-maintaining** — the runtime tracks its own state, errors, gates, costs, risks, and regressions;
- **self-improving** — failures from traces become tests, gates, skills, and runtime changes;
- **self-iterating** — the agent can branch hypotheses, validate them, kill bad paths, and write no-edge/blocked reports instead of spinning forever;
- **evidence-first** — progress is backed by tools, files, logs, tests, citations, ledgers, and verifiers, not just fluent summaries;
- **first-principles by default** — every long run must repeatedly reduce the mission to load-bearing truths, constraints, causal mechanisms, and failure modes instead of copying surface instructions;
- **critical and questioning** — the agent must challenge its own assumptions, challenge weak user phrasing when needed, seek disconfirming evidence, and treat easy-looking paths as hypotheses to test rather than facts;
- **shortest reliable path** — the runtime should prefer the smallest action that advances the first failed gate, reusing mature tools and existing work before building new infrastructure.

In short: `/supergoal` should not mean “continue until the judge says done.” It should mean:

> Continuously maintain a verifiable mission state, choose the next step that best reduces uncertainty/risk/cost, and automatically change path or stop when evidence shows the current path is exhausted.

## Core principles

### 1. Budget is not intelligence

Large budgets are only useful if the runtime can spend them well. `/supergoal` must therefore optimize for convergence, not activity. It should prefer the highest information-gain action, not the safest-looking busywork.

### 2. First principles before execution

`/supergoal` should not blindly execute the literal wording of a request. It should first ask:

- What is the user actually trying to achieve?
- What must be true for this mission to succeed?
- What are the load-bearing constraints, causal mechanisms, and failure modes?
- Which assumptions are unverified?
- What evidence would falsify the current direction?

First-principles reasoning is not extra philosophy. It is how a long-running agent avoids optimizing a local subtask while missing the global mission.

### 3. Critical spirit and questioning are mandatory

A long-running agent must be willing to question:

- its own plan;
- the critic's JSON;
- previous summaries;
- apparent progress;
- convenient assumptions;
- whether the requested implementation is even the right path.

This does **not** mean being argumentative with the user. It means treating every major action as a falsifiable hypothesis and actively looking for counterevidence before spending more budget.

### 4. Shortest reliable path beats heroic autonomy

The best supergoal step is usually not the most impressive step. It is the shortest reliable step that advances the first failed gate.

Preferred order:

1. Reuse existing mature solutions if they satisfy the mission.
2. Make the smallest tool-backed diagnostic that reduces uncertainty.
3. Verify one artifact before expanding scope.
4. Build new infrastructure only when it is proven to be a dependency.

A supergoal that builds a large platform before testing the smallest viable hypothesis is failing the shortest-path principle.

### 5. State beats prompt length

A long prompt is not a durable runtime. Important mission facts must live in structured state:

- root intent and success criteria;
- gates and acceptance criteria;
- research ledger;
- hypothesis portfolio;
- plan status and verifier state;
- tool calls, artifacts, logs, costs, and risk events;
- blockers, no-edge conclusions, and reusable lessons.

### 6. Evidence beats self-report

LLM critic JSON is useful for diagnosis, but it is not proof. Research sufficiency and plan progress must be derived from tool-backed evidence where possible.

This patch extends `ResearchFinding` with provenance fields such as `tool_call_id`, `retrieved_at`, `query`, `evidence_quote_or_hash`, relevance, and contradiction flags. Critic-extracted findings can appear on the board as hints, but they cannot satisfy the research gate without provenance.

### 7. Gates beat vibes

A supergoal should move through explicit mission gates. For strategy/research tasks, examples include:

- enough hypotheses exist;
- each hypothesis has a baseline, experiment, kill criteria, artifact, and verdict;
- if no hypothesis passes, a no-edge attribution report exists;
- infrastructure work is allowed only when it proves dependency on an open gate.

This prevents the classic failure mode: “the agent did real work, therefore the plan is done.” Real work is not enough; the right gate must pass.

### 8. Replan must constrain action

A replan prompt that only says “think again” is weak. Replanning must change the action space:

- stop repeating the same action class;
- compare alternatives;
- satisfy the first failed gate;
- avoid infrastructure unless it is a proven dependency;
- produce a blocked/no-edge report if the gate cannot be satisfied.

### 9. Hypotheses are first-class objects

For trading, research, debugging, and scientific exploration, a flat list of “hypotheses” is not enough. `/supergoal` needs a portfolio:

- claim;
- why plausible;
- data needed;
- baseline;
- experiment;
- kill criteria;
- expected value/edge;
- risk;
- status;
- artifacts;
- verdict reason.

This is what prevents “I built another validator” from masquerading as “I tested a strategy.”

### 10. Stop conditions are features, not failures

A good long-running agent must know when to stop, branch, ask, or write a no-edge report. Infinite continuation is not autonomy; it is control failure.

### 11. Normal `/goal` must stay simple

`/supergoal` should not pollute ordinary `/goal`. The lightweight path should remain fast, predictable, and easy to reason about. Supergoal-only machinery is guarded by `mode == "supergoal"`, with regression tests proving normal goal behavior still works.

### 12. Safety must become a first-class runtime layer

Long-running agents with shell, file, network, API, database, or trading permissions need pre-execution policy, not only post-hoc judging. This patch adds gates/inertia guards, but a full permission/scope/destructive-action policy is still future work.

## What this patch currently changes

1. **Compression-safe goal runtime migration**
   - Adds `migrate_goal_state(old_session_id, new_session_id, reason="compression")`.
   - Moves active `/goal` or `/supergoal` state across context-compression session splits.
   - Leaves the old session as a `migrated` audit tombstone.
   - Copies goal event history to the new session and appends a `migrated` event.

2. **Supergoal Mission Control state**
   - Adds durable dataclasses for:
     - `PlanStep`
     - `GoalEvent`
     - `ResearchFinding`
     - `HypothesisRecord`
     - `GoalGate`
   - Extends `GoalState` with research ledger, hypothesis portfolio, gates, action history, current action class, hard gate reason, and no-edge report.

3. **Tool-backed research gate**
   - `research_sufficiency` is derived from tool-backed findings.
   - Critic-only source claims stay visible but do not pass the gate.

4. **Hard gates and strategy gates**
   - Default gates track intent, research, verified artifact, and final evidence/no-edge outcome.
   - Strategy/trading-style goals add `SG-1..SG-4` for hypothesis portfolio and no-edge reporting.
   - Subgoals can incrementally add strategy gates after the run starts.

5. **Action taxonomy + inertia guard**
   - Classifies actions as research, hypothesis generation, experiment execution, validation, infrastructure, reporting, safety, or unknown.
   - Blocks infrastructure inertia while strategy gates remain open.
   - Injects a hard-gate block into the next continuation prompt.

6. **Verifier-backed plan progression**
   - Plan steps no longer become done just because critic says `progress=real`.
   - Open gates keep the current step in progress.
   - The completion judge cannot mark a supergoal done while deterministic gates remain open.

7. **Codex-like checkpoint pressure**
   - Replan prompts require compact evidence sections: Changed, Verified, Evidence, Remaining gates, Next action class, and why not the tempting alternative.

8. **Status/UX upgrades**
   - `/supergoal status` surfaces gate counts, first failed gate, hard gate reasons, diagnostics, events, and next action.
   - Gateway continuation messages distinguish `supergoal` from normal `goal`.

## Repository layout

```text
patches/supergoal-runtime.patch             # apply this to Hermes Agent
overlay/                                    # full modified files for review/reference
  hermes_cli/commands.py
  hermes_cli/config.py
  hermes_cli/goals.py
  hermes_cli/goal_events.py
  hermes_cli/supergoal/__init__.py
  hermes_cli/supergoal/domain.py
  hermes_cli/supergoal/evaluators.py
  hermes_cli/supergoal/controller.py
  hermes_cli/supergoal/store.py
  hermes_cli/supergoal_gates.py
  hermes_cli/supergoal_projection.py
  agent/conversation_compression.py
  gateway/run.py
  tests/hermes_cli/test_goals.py
  tests/hermes_cli/test_supergoal_command_registry.py
  tests/gateway/test_goal_verdict_send.py
  tests/gateway/test_goal_status_notice.py
  tests/gateway/test_supergoal_max_turns_config.py
docs/run-analysis.md                        # observed failure analysis from the live run
docs/architecture.md                        # architectural rationale
docs/principles.md                          # why Supergoal exists and what principles guide it
docs/completion-gate-conflict.md            # real completion/gate conflict diagnosis
scripts/apply.sh                            # helper to apply the patch to a Hermes checkout
reference-runs/                             # redacted real run logs and state snapshots
```

## Apply

From a Hermes Agent checkout:

```bash
/path/to/supergoal-runtime/scripts/apply.sh /path/to/hermes-agent
```

Or manually:

```bash
cd /path/to/hermes-agent
git apply /path/to/supergoal-runtime/patches/supergoal-runtime.patch
```

## Test

From the Hermes Agent checkout after applying:

```bash
PYTHONPATH=. pytest tests/hermes_cli/test_goals.py -q
PYTHONPATH=. pytest \
  tests/gateway/test_goal_verdict_send.py \
  tests/gateway/test_goal_status_notice.py \
  tests/gateway/test_supergoal_max_turns_config.py \
  tests/hermes_cli/test_supergoal_command_registry.py \
  tests/gateway/test_goal_max_turns_config.py \
  tests/tui_gateway/test_goal_command.py \
  tests/cli/test_cli_goal_interrupt.py -q
PYTHONPATH=. pytest tests/hermes_cli/test_kanban_goal_mode.py -q
python -m py_compile hermes_cli/goals.py agent/conversation_compression.py gateway/run.py cli.py
git diff --check
```

Verified locally before packaging:

```text
tests/hermes_cli/test_goals.py: 68 passed, 1 warning
goal/gateway/TUI/CLI regression subset: 100 passed
kanban goal-mode regression: 12 passed
py_compile + git diff --check: passed
```

## Compatibility notes

- This patch assumes Hermes Agent already has `/goal` and `/supergoal` support in `hermes_cli/goals.py` and context compression in `agent/conversation_compression.py`.
- It is backwards-compatible with older stored `GoalState` rows: missing fields default safely.
- Normal `/goal` remains intentionally lightweight. Supergoal-only behavior is guarded by `mode == "supergoal"`.
- Compression migration applies to both `/goal` and `/supergoal` because both share the same durable state boundary.

## Current limitations / next work

This patch is not the final “superintelligent long-running agent.” It is the control-system foundation.

Important next steps:

1. **Logical `goal_run_id`** — make goal identity stable across physical session IDs, child sessions, compression, and resumes.
2. **Pre-execution permission policy** — scope, destructive-action controls, trading/API/database risk policy before tools run.
3. **Dedicated model roles** — split completion judge, strategic critic, planner, policy guard, and artifact verifier.
4. **Tool wrappers that write ledgers directly** — web/search/GitHub/paper/local wrappers should emit research/artifact ledger entries without relying on critic extraction.
5. **Full board commands** — `/supergoal board`, `/supergoal gates`, `/supergoal portfolio`, and `/supergoal next` for user-visible mission control.
6. **Trace → feedback → evals loop** — mine real failed runs into reusable regression tests and ranked harness changes.

## License

This package is intended to be used with Hermes Agent. See `LICENSE`.
