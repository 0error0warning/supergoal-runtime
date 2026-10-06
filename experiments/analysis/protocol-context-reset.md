# Supplementary context reset experiment

Designed after observing the original matrix's early single-episode ceiling.
This is a new exploratory study; it does not repair or replace that primary
result. Freeze this script and record its fixture hashes before any live run.

Six runs: two new data fixtures (8601/8602), each with three context policies:

- `minimal_continue`: original goal on the first episode, generic continue on
  subsequent fresh sessions, with workspace and public goal file still accessible.
- `original_goal`: resend the full original goal every session, a simple strong
  baseline for reconstructing intent without a structured progress summary.
- `durable_state`: the frozen v2 goal, turn count, remaining budget and last
  public acceptance result supplement the generic continuation prompt.

All policies use identical frozen kernel, public acceptance, real Hermes/SWE2,
tools, staged inputs, workspace persistence and maximum budgets. Previous
conversation/history/tool-record files move outside the executor namespace
before each new SDK process. Archive the isolated Hermes home too, because the
host can persist transcripts in its own database; each episode receives a fresh
home. Do not remove workspace artifacts or hide the public
task file from any policy. Model access to that file is legitimate recovery.

Deliver one CSV shard at each of three work boundaries, without artificial
hour-long sleeps. Score exact output and fresh script reproduction independently
after each phase, using only then-available inputs. Do not return hidden scores.
The primary supplementary outcome requires correct output at every phase and
final verified stop. Also report final-only correctness, prompt bytes, model
requests, known tokens, errors and any additional repair episodes.

Randomize policy order within fixture using seed 50105. Keep all attempts.
Registration with hashes and the complete plan is written before the first call.
Two related fixtures and one sample per cell support only descriptive statements.
This experiment tests reconstruction across fresh sessions, not large-context
reasoning, context compression, autonomous planning, or real elapsed duration.
The detailed task semantics derive from the primary data family, so it is not
an independent-domain generalization test.
