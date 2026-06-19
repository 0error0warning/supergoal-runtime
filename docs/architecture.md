# Architecture Notes

## Problem

The live `/supergoal` run showed that a high-budget loop can make a lot of local progress while failing the real mission.

The observed failure was not “the model was not smart enough.” It was architectural:

- state was keyed by physical `session_id`, while context compression rotates sessions for the same logical run;
- research sufficiency was based on critic assertion rather than tool provenance;
- replanning was prompt guidance, not an action constraint;
- plan steps could be marked done by generic `progress=real` even when acceptance artifacts were missing;
- there was no first-class hypothesis portfolio for research/trading missions;
- infrastructure work could repeat because no action taxonomy or inertia guard blocked it;
- judge and critic shared the same auxiliary route by default;
- permission/scope/safety policy was not yet a first-class runtime layer.

This patch moves `/supergoal` toward a Mission Control architecture: explicit state, gates, ledgers, portfolios, verifiers, action classes, and hard stops.

## Target model

```text
GoalRun / Mission
  IntentContract
    root_intent
    success_criteria
    anti_goals
    constraints
    permissions
    stop_conditions

  ResearchLedger
    findings[]  # tool-backed provenance, quote/hash, relevance, contradiction

  HypothesisPortfolio
    hypotheses[]
      claim
      baseline
      experiment
      kill_criteria
      artifacts
      verdict

  PlanDAG / PlanSurface
    tasks[]
      action_type
      dependencies
      required_artifacts
      verifier

  ExecutionLedger
    tool_calls
    file_changes
    tests
    logs
    costs
    risk_events

  Evaluator
    deterministic_gates
    completion_judge
    strategic_critic
    policy_guard
    artifact_verifier
```

The runtime should find the first failed gate, choose the next action class, execute only work that advances that gate, verify artifacts, and stop/branch/report when evidence says the path is exhausted.

Three reasoning principles sit above the mechanics:

1. **First-principles reduction** — repeatedly reduce the mission to root intent, causal model, constraints, and failure modes before spending another turn.
2. **Critical/questioning stance** — treat plans, critic outputs, apparent progress, and convenient assumptions as hypotheses requiring evidence.
3. **Shortest reliable path** — prefer the smallest evidence-producing action that advances the first failed gate; reuse before build, diagnose before platform, verify before expansion.

## Main implementation points

### Compression-safe logical goal identity

The runtime now treats `goal_run_id` as the stable mission identity and `session_id` as a physical context version.

Storage model:

```text
goal_run:{goal_run_id}          # authoritative mission state
goal_session:{session_id}       # physical session → logical run binding
goal_events:{goal_run_id}       # append-only mission event log
goal:{session_id}               # compatibility mirror for old callers/tools
```

`migrate_goal_state(old_session_id, new_session_id, reason="compression")` binds the new physical session to the existing logical run and appends a `session_rotated` event. It does not fork or copy the mission as a new logical run, and it preserves an already-active destination run instead of overwriting it.

### Explicit controller pipeline

`hermes_cli/supergoal/controller.py` is now the post-turn control boundary for `/supergoal`:

```text
Observe → Project → Evaluate → Reconcile → Decide → Render
```

The controller owns the high-risk runtime side effects that previously lived inside the large GoalManager body:

- completed-turn accounting and `last_turn_at`;
- raw `last_verdict` / `last_reason` mirror;
- judge parse/API health counters;
- critic failure counter and periodic replan;
- active-continue persistence, `turn_evaluated` event, and continuation prompt generation;
- DONE state mutation, persistence, and `done` event;
- PAUSED guard state mutation, persistence, and `paused` event.

The old `legacy_decider` injection has been replaced by an explicit `apply_runtime_outcome` adapter. The remaining adapter exists only as a staged compatibility seam for gate/plan reconciliation and decision rendering while those helpers are slimmed further.

Important invariant: DONE side effects are keyed on `runtime["verdict"] == "done"`, not `runtime["status"] == "done"`, so re-evaluating an already-completed supergoal (`status=done`, `verdict=inactive`) cannot duplicate the `done` event.

### Mission Control state

The patch adds or extends these runtime objects:

- `PlanStep` — compact plan surface;
- `GoalEvent` — append-only audit events;
- `ResearchFinding` — provenanced research/evidence item;
- `HypothesisRecord` — executable hypothesis portfolio entry;
- `GoalGate` — deterministic gate with verifier and evidence;
- `GoalState` — stores ledgers, gates, portfolio, action history, hard-gate reason, and no-edge report.

### Tool-backed research sufficiency

`ResearchFinding` now supports:

- `source_type`
- `title`
- `locator`
- `claim`
- `retrieved_at`
- `tool_call_id`
- `query`
- `evidence_quote_or_hash`
- `relevance_score`
- `contradiction`

The effective `research_sufficiency` is derived from tool-backed findings only. Critic-only findings remain visible but cannot pass the gate.

### Hard gates

Default gates:

- `G1` — intent contract captured;
- `G2` — tool-backed research ledger sufficient;
- `G3` — verified execution artifact exists;
- `G4` — final evidence/no-edge/blocked outcome exists.

Strategy/trading-style goals add:

- `SG-1` — at least 3 hypotheses;
- `SG-2` — each hypothesis has baseline, experiment, kill criteria, artifact, verdict;
- `SG-3` — if none pass, no-edge attribution report exists;
- `SG-4` — infrastructure work requires dependency proof.

Subgoals can add strategy gates incrementally after the run starts.

### Action taxonomy and inertia guard

The runtime classifies actions as:

- `research`
- `hypothesis_generation`
- `experiment_execution`
- `validation`
- `infra_engineering`
- `reporting`
- `safety`
- `unknown`

If strategy gates are open and the agent keeps proposing infrastructure work, the hard gate blocks that action class and forces work back to the first failed strategy gate.

### Verifier-backed plan progression

Plan steps no longer advance just because the critic says `progress=real`.

Open gates keep the current step in progress. A completion judge cannot mark the supergoal done while deterministic gates remain open.

### Normal `/goal` isolation

The new machinery is guarded by `mode == "supergoal"`. Normal `/goal` remains lightweight and uses its original continuation prompt and basic judge flow.

## Tests

The current patch adds or updates tests for:

- supergoal prompt/status/event behavior;
- critic board updates;
- literalism and thin/missing research replans;
- tool-backed research sufficiency;
- hypothesis portfolio gates;
- infrastructure inertia guard;
- subgoal-triggered strategy gates;
- completion judge not bypassing open supergoal gates;
- compression goal-state migration;
- normal `/goal` regression;
- gateway continuation label and enqueue behavior.

Verified for the current patch package:

```text
Hermes core checkout:
  py_compile goals/controller/domain: passed
  tests/hermes_cli/test_goals.py + tests/supergoal_replay/ + tests/gateway/test_goal_verdict_send.py: 132 passed

Clean-base patch verification from the pinned base:
  git apply --check: passed
  git apply: passed
  py_compile goals/controller/domain: passed
  same focused test set: 132 passed

supergoal-runtime GitHub Actions:
  fa1c43b7182110bc8c1ea177a8361158273b5822: success
```

## Remaining architectural work

1. Slim the remaining GoalManager adapter callbacks for gate/plan reconciliation.
2. Tool wrappers that write ledgers directly.
3. Dedicated model roles for judge/critic/planner/policy/verifier.
4. Richer pre-execution permission and scope policy configuration.
5. Full user-facing Mission Control commands.
6. Trace → feedback → evals → ranked changes → implementation handoff loop.
