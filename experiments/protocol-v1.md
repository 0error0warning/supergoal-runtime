# Protocol v1 — 2026-10-05

Status: registered before task-level live experiments. API smoke checks are
infrastructure checks and are excluded from all task results.

## Question and claim boundary

Does Supergoal improve independently verified completion of finite tasks over
the same Hermes/SWE2 executor, without increasing false completion, and can it
recover correctly from interruptions and real waiting? The aim is to test this
hypothesis, including negative results, rather than to obtain a predetermined
positive conclusion.

Host: the user's customized Hermes v0.21.3 release. Model: `devin/swe-2` through
the existing Devin provider. The user reports no API charge; record tokens,
requests, elapsed time and infrastructure overhead regardless. No provider or
model fallback is permitted. Provider responses identify an alias rather than
an immutable model checkpoint; record this reproducibility limitation.

## Experimental units

Pair arms on identical generated task fixtures, repeated with fresh workspaces
and sessions. Families: software repair, data reconciliation, and synthesis of
conflicting local source documents. Generated fixtures make outcomes checkable
and exclude production data; they are a controlled benchmark, not a substitute
for public benchmark replication or independent real-world assignments.

Use development seeds 1101/1102 for debugging. Reserve evaluation seeds
7201/7202/7203. Freeze generator, task hashes, scorer, prompts, limits and arm
definitions before evaluating those seeds. Report family-wise results. Seeds
control fixtures/order, not model sampling; don't claim deterministic inference.

Initial arms:

- `hermes`: native Hermes tool loop, one task run.
- `native_goal`: the installed Hermes GoalManager and its actual goal judge.
- `sg_v1`: the frozen local generic-runtime candidate from before this study.
- `sg_v2`: durable control, structured task state and grounded acceptance.
- `sg_v2_no_context`: identical v2, omitting task-state context reinforcement.
- `sg_v2_no_verification`: identical v2, omitting grounded acceptance; the
  executor's terminal response ends the run. Hidden outcome scoring still runs.

The no-verification arm removes a whole mechanism, not only one prompt. State
persistence is assessed separately under faults, with a stateless recovery
control where appropriate, so ordinary task success is not used to claim crash
recovery. Report prompt/token differences induced by each intervention.

Start with one concurrent agent, at most 6 outer turns and 48 executor model
calls per task, and a 20-minute wall limit per task. Evaluator calls also count
toward a 60 total model-call ceiling, with at most 3 evaluation attempts per
candidate. Per-call timeout and model output caps are fixed across arms.
Unused resources are not spent simply to match consumption. Describe any host
extra calls or accounting gaps explicitly. Context stress and longer limits
are separate, labeled conditions rather than silently changed limits.

Randomize arm order within task/replicate blocks using seed 41005. Runs are
serial to avoid server resource and provider concurrency confounds. Record
block order, model response identity, usage, tool events, exit reason and hashes.

## Independent measurement

Primary: all mandatory hidden outcome checks pass. Secondary: check coverage,
false completion (runtime declares success but hidden checks fail), underclaim
(checks pass without success declaration), model/tool calls, tokens (including
evaluators), wall time, and additional work after earliest measured success.
Score each candidate at a boundary without returning hidden feedback to the
agent; label boundary resolution when discussing extra work.

Infrastructure errors remain in the end-to-end denominator and also appear in
a separate infrastructure table. Repeat only according to a uniform recorded
transport-retry rule; retain the first attempt and all failures. No selective
replacement of unsuccessful task attempts.

Report paired differences and raw paired outcomes, plus uncertainty intervals
when there are enough independent tasks. Repeated runs of the same fixture are
not independent tasks. Small pilot proportions must not be marketed as a
general success rate or statistical proof. Tune on development tasks only.

## Reliability and duration

Deterministic tests cover duplicate receipts, two owners, old-owner writes,
commit/dispatch crash windows, late tool events, invalid judge output, changed
artifacts, cancellation and waiting wakeup. Live tests include a process restart
between real Hermes turns and a delayed input arrival. A supervisor restart
test must start a new process and reopen persisted state.

Run a separate staged soak with a real elapsed delay of at least two hours,
multiple input deliveries and one worker restart. Count active model work,
waiting time and elapsed duration separately. An overnight/24-hour study and
public benchmark replication remain follow-up evidence until actually run;
sleeping for two hours is not two hours of independent cognitive work.

## Iteration and release

Freeze the old candidate before edits. Fix observed failures with regression
tests and record changed mechanisms. Freeze a v2 candidate before reserved
evaluation. A failed held-out evaluation becomes historical evidence; any
further tuning requires a new version and new reserved tasks.

Only experiment code, anonymous fixtures, aggregates and reproducibility
metadata are versioned. The production release, production chats, channels and
default plugin configuration are outside the experimental deployment.

## Infrastructure amendment after development smoke (2026-10-05)

The six code/1101 attempts in dev01 retain the original host stream-idle default
(12 seconds at their context size). Five of six ended at one boundary; all six
artifacts passed hidden checks, but two runs ended without declaring completion.
Recorded transport timeouts prevent using this batch for efficacy conclusions.
For subsequent batches set the same 90-second event-idle, first-event and stream
read timeouts in every arm. Do not patch production Hermes. Executor output cap
is 3000 tokens, and one native loop can use all remaining executor calls. Record
physical retries independently; never equate host logical calls with requests.

The model proxy rejects every model except devin/swe-2, enforces the physical
request ceiling, and retains usage after a client disconnect when the upstream
still completes. Missing usage is reported as unavailable, never zero. Each
subsequent version runs from a frozen server bundle. A global file lock serializes
model work across the task matrix and the staged two-hour test.

The first driver incorrectly passed a bare task prompt to v1's automatic-turn
controller. V1 classified it as a real user preemption and paused before judging.
V1 rows from dev01/candidate-r1 are therefore invalid integration controls, not
evidence of poor agent ability. Candidate-r2 uses v1's real start_for_command
envelope and claims its continuation before execution. The assigned objective
is identical across arms; v1's startup board/prefix is part of its actual harness
and its prompt tokens count toward measured consumption. Rerun v1 development
controls after this correction; reserved evaluation has not started.

Candidate-r2 moves authoritative v2 state and lease ownership out of the executor
namespace. Acceptance runs in a separate process after the executor exits, with
read-only workspace mounts and no model access. This uses the same kernel and
checks; it removes the worker's ability to edit its control database. Candidate-r3
adds confined artifact reads and normalizes acceptance-process failures to unknown.
Study artifacts are capped at 5 MB each. Hidden code scoring also enforces exact
JSON numeric types and checks ordinary imports against the standard library;
dynamic-import/adversarial evasion is outside this benchmark. Freeze r3 before
reserved execution, and keep earlier development rows labeled by bundle version.

Before reserved execution, the executor mount is reduced to worker.py,
study_hooks.py, check_worker.py and grade_code.py. Earlier development bundles
mounted the whole study directory, exposing the generator and future soak data
in source form even though private fixture JSON was outside the namespace.
Those batches are apparatus debugging only. The reserved-study preflight must
confirm that tasks.py, supervisor.py, soak.py, hidden cases and the authoritative
kernel database are unavailable to the executor.
Input directories, source documents and specification files are read-only mounts.
In the soak, only the host can deliver batches or create the CLOSED marker.
