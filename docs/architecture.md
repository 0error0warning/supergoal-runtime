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

## Main implementation points

### Compression migration

`migrate_goal_state(old_session_id, new_session_id, reason="compression")` copies runtime state across session splits and leaves the old state as a `migrated` audit tombstone.

This is a transitional fix. The long-term model should use a stable `goal_run_id`, with `session_id` treated as only one physical context version.

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

Verified locally:

```text
tests/hermes_cli/test_goals.py: 68 passed, 1 warning
goal/gateway/TUI/CLI regression subset: 100 passed
kanban goal-mode regression: 12 passed
py_compile + git diff --check: passed
```

## Remaining architectural work

1. Stable logical `goal_run_id`.
2. Tool wrappers that write ledgers directly.
3. Dedicated model roles for judge/critic/planner/policy/verifier.
4. Pre-execution permission and scope policy.
5. Full user-facing Mission Control commands.
6. Trace → feedback → evals → ranked changes → implementation handoff loop.
