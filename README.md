# Supergoal Runtime for Hermes Agent

A focused patch package for improving Hermes Agent `/supergoal` as a durable long-running-agent runtime instead of a larger `/goal` prompt.

This repository is intentionally an **overlay / patchset**, not a full fork of Hermes Agent. The code is designed to be applied to `NousResearch/hermes-agent` and kept reviewable as a small, auditable change.

## What this changes

1. **Compression-safe goal runtime migration**
   - Adds `migrate_goal_state(old_session_id, new_session_id, reason="compression")`.
   - Moves active `/goal` or `/supergoal` state across context-compression session splits.
   - Leaves the old session as a `migrated` audit tombstone.
   - Copies goal event history to the new session and appends a `migrated` event.

2. **Research evidence ledger for `/supergoal`**
   - Adds `ResearchFinding` with `source_type`, `title`, `locator`, and `claim`.
   - Persists `research_findings` in `GoalState`.
   - Renders findings in the Supergoal State Board.

3. **Evidence-gated research sufficiency**
   - A critic can no longer mark research as `sufficient` by assertion alone.
   - Sufficiency is derived from source diversity:
     - `paper + github`, or at least 3 external source types → `sufficient`
     - partial external evidence → `thin`
     - no external evidence → `missing`

4. **Better long-horizon planning surface**
   - Expands the state board with root intent, success definition, first-principles model, existing-solution scan, build-vs-reuse decision, literalism risk, and research sufficiency.
   - Changes the default `/supergoal` plan from generic execution to: infer intent → first-principles model → scan existing solutions → execute shortest reliable path → verify → finalize.
   - Strengthens replan prompts to avoid inertia and compare alternatives including reuse.

## Repository layout

```text
patches/supergoal-runtime.patch             # apply this to Hermes Agent
overlay/                                    # full modified files for review/reference
  hermes_cli/goals.py
  agent/conversation_compression.py
  tests/hermes_cli/test_goals.py
docs/run-analysis.md                        # observed failure analysis from the live run
docs/architecture.md                        # architectural rationale
scripts/apply.sh                            # helper to apply the patch to a Hermes checkout
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
PYTHONPATH=. pytest   tests/gateway/test_goal_verdict_send.py   tests/gateway/test_goal_status_notice.py   tests/gateway/test_supergoal_max_turns_config.py   tests/hermes_cli/test_supergoal_command_registry.py -q
python -m py_compile hermes_cli/goals.py agent/conversation_compression.py
git diff --check
```

Verified locally before packaging:

```text
61 passed, 1 warning
gateway/supergoal regression subset: 13 passed
py_compile + git diff --check: passed
```

## Compatibility notes

- This patch assumes Hermes Agent already has the `/goal` and `/supergoal` runtime in `hermes_cli/goals.py` and context compression in `agent/conversation_compression.py`.
- It is backwards-compatible with older stored `GoalState` rows: missing new fields default safely.
- It avoids changing normal `/goal` prompt behavior except for compression migration, which applies to both `/goal` and `/supergoal` because both share the same durable state boundary.

## License

This package is intended to be used with Hermes Agent. See `LICENSE`.
