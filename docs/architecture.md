# Architecture Notes

## Problem

The live run showed that `/supergoal` behaved like a high-budget `/goal`: it kept making local engineering progress, but did not reliably preserve strategic state, external research coverage, or runtime continuity across context compression.

The failure was architectural, not scenario-specific:

- Goal state was keyed by physical `session_id`, while compression rotates `session_id` for the same logical conversation.
- Research sufficiency was a critic assertion, not an evidence-backed runtime property.
- Replanning was prompt-level guidance without enough durable state to break path inertia.

## Design principles

1. **Runtime state must follow logical conversation boundaries**

Compression should be treated like a session-id rotation, not a fresh goal lifecycle. Anything that is part of the long-running runtime — goal state, event log, current plan, research ledger — must migrate when the transcript migrates.

2. **Research quality needs a ledger, not an adjective**

`research_sufficiency = sufficient` is only meaningful if it is derived from provenance. The patch adds compact `ResearchFinding` records and derives sufficiency from source diversity.

3. **Replanning must change the search space**

A replan should not mean "continue with a slightly different local task". The state board now exposes root intent, first principles, existing solution scan, build/reuse decision, and research findings so future continuations can choose a genuinely different next step.

4. **Patch surface should be small and reviewable**

This is not a fork. The package is a patchset with overlay files, tests, and docs. It can be converted into an upstream PR or maintained as a local customization.

## Main implementation points

### `ResearchFinding`

A small dataclass stored in `GoalState.research_findings`:

- `source_type`: `paper`, `github`, `web`, `docs`, `repo`, `benchmark`, `local`, or `other`
- `title`
- `locator`: URL, arXiv ID, path, or other stable handle
- `claim`: short statement of what the source changes

### Evidence-gated research sufficiency

The critic can emit `research_sufficiency`, but the runtime derives the effective value:

- `sufficient`: `paper + github`, or at least 3 external evidence types
- `thin`: some external evidence, not enough diversity
- `missing`: no external evidence

### Compression migration

`migrate_goal_state()` copies runtime state from old session to new session and marks the old state as `migrated`. This preserves auditability without leaving two active goal states.

### Tests

The test additions cover:

- critic board updates for new state fields
- forced replan on literalism/thin research
- source-diversity gate for research sufficiency
- goal-state migration across compression session split
- existing supergoal prompt/status/event behavior
