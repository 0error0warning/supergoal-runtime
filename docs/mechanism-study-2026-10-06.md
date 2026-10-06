# Supergoal mechanism study 01 — development protocol

This protocol is exploratory architecture research. The existing public-gcp01,
public-parallel24-v1 and oneday-transfer01 candidates and original outcomes remain
unchanged. These inspected tasks are development data, not a fresh holdout.

## Evidence motivating the experiments

- The current `sg_no_context` removes the durable-state prompt but retains the
  complete executor history. Its result cannot establish that durable external
  state is unnecessary, or that a fresh process can reconstruct the task.
- A stopped executor is reviewed in a filesystem snapshot. Running services and
  mounts are absent, and a read-only root can obstruct validation commands.
  The review's scope is therefore narrower than the complete task environment.
- A first `unknown` currently ends the run. Parser failure, insufficient reviewer
  budget, a missing observation and an actually incomplete deliverable must not
  be treated as the same event.
- Actual CPU use sampled at 17:42 UTC was 56.5%; available RAM was 60,576 MiB.
  Static CPU reservations include both executor and auditor despite sequential
  model phases. Some task containers do run background compilation, so CPU phases
  may only be shared after the executor is explicitly paused.
- A OneDay spreadsheet grader failed before producing a score. A cgroup OOM at
  17:29:23 UTC is consistent with the failure timing and 4 GiB limit; its container
  was removed without saving its exit state. Treat this as apparatus failure,
  not a model zero. An isolated, separately recorded memory audit is needed.

## Primary literature and what it changes in this design

[An Empirical Study of Harness Design for Coding Agents](https://arxiv.org/abs/2609.20804)
(17 September 2026) varies context management, planning and action interfaces
under a fixed loop. Its results motivate mechanism-level interventions and joint
reporting of capability and resource consumption. Here we use a 2×2 context
policy experiment, record how often the intervention actually activates, and
avoid interpreting an unchanged one-episode run as evidence about long-horizon
state recovery. Its reported results concern other models, not SWE2.

[LongHorizon-Harness](https://arxiv.org/abs/2608.01964)
externalizes task state and uses fresh execution episodes with independent
auditing. This motivates a state-sufficiency test. The present Supergoal state is
only the original goal, turn budget and latest acceptance report; it is not yet
the paper's structured requirement/fact ledger. The paper's headline gains do
not establish the effect of every component independently.

[SoL-Pi](https://arxiv.org/abs/2609.20519) (17 September 2026) separates mechanism
search from frozen evaluation and evaluates efficiency subject to capability
constraints. We adopt that separation: development failures may guide changes;
subsequent holdout outcomes may reject a candidate but will not be patched into
that same holdout. We report actual tokens and requests even when the endpoint
has no model charge. Model cost does not represent VM cost.

[mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930)
is an actual upstream control, version 2.4.6. Its DefaultAgent, mini.yaml prompts,
Responses tool parser, observation formatting, termination and trajectory
serialization are retained. Adaptations are the SWE2 streaming endpoint and
execution inside a Harbor-owned container with a 120-second command timeout.
This is an adapted upstream harness comparison, not its official leaderboard
configuration. LongHorizon source is also pinned at
`a1dd930614972b92361c1b9cd6aac441a6db5a65`; no live comparison result is claimed yet.

## Registered interventions

| Condition | Executor history across episodes | Durable-state prompt | Audit |
|---|---|---|---|
| native | Native Hermes episode | No | None |
| mini_swe | Upstream mini episode | No | None |
| sg_v2 | Retained | Yes | One independent audit |
| sg_no_context | Retained | No | One independent audit |
| sg_fresh | Fresh | Yes | One independent audit |
| sg_fresh_no_context | Fresh | No | One independent audit |
| sg_retry_unknown | Retained | Yes | At most one re-audit, only after unknown |

The context intervention is a harness prompt/history policy: removing the state
also changes the reminder wording. It does not isolate the abstract idea of
memory from all prompt effects. Re-auditing is a bounded extra-computation policy;
its result must be interpreted together with extra requests and time.

Four different public development tasks: configure-git-webserver (services),
dna-assembly (constraint-heavy science), write-compressor (algorithm/delivery),
extract-elf (reverse engineering). The last task was selected after observing
development failures; selection is explicitly outcome-informed. One run per
task/condition gives 28 rows, not 28 independent tasks. No added seeds or best-of-N.

All conditions use SWE2, 192 shared upstream request reservations, requested
6,000 output tokens per call, six outer episodes where applicable, and the
official task CPU, RAM, solver timeout and hidden final verifier. Reviewer and
transport retries consume the same request cap. Equal caps do not imply equal
realized compute. Native and mini use their own stopping rules; neither is forced
to spend unused calls.

All new SG conditions pause the actor container throughout auditing and unpause
it afterwards, including error paths. This binds the audit to a stable actor
filesystem and makes actor/reviewer CPU phases exclusive. It does not preserve
live service state in the review snapshot. This shared engineering change and the
SQLite compatibility fix apply to every new SG condition; old-study outcomes are
not pooled as randomized controls for this new cohort.

## Capacity, measurements and decision rules

The new cohort uses the two CPU slots released when public-gcp01 finishes; the
other cohort's six-CPU pool is unchanged. At most two 1-CPU tasks execute here,
with paused actors during SG review. Admission observes host RAM/disk reserves
and the existing absolute cloud stop at 2026-10-06 14:24:06 UTC. No instance size,
cloud stop time or local Docker installation changes.

Primary outcomes: official per-task reward and end-to-end completion. Secondary:
actual request reservations, completed responses, input/output/cache tokens,
wall time, executor/auditor requests, number of executor episodes, unknown/dispute
causes, and false acceptance (SG says succeeded, official score is below 1).
Keep apparatus failures separate, preserve raw scores and classify controller
errors in end-to-end outcomes. Report both the whole assigned cohort and the
subset where a second execution episode activated the context intervention.

With only four development task units, effect estimates are descriptive. No
statistical significance or generalization claim is justified. Keep every
trajectory and failed integration check. A promising change must next survive
fresh task IDs with frozen code. No candidate is promoted based only on these
four tasks. A capability loss cannot be hidden by lower average token use.

The durable-state hardening is independently required: the server links SQLite
3.49.1. [SQLite's WAL-reset advisory](https://sqlite.org/wal.html#walresetbug)
documents an affected concurrent-write/checkpoint range. The new Kernel chooses
DELETE journaling on affected versions and WAL only on fixed branches. This is
a compatibility mitigation, not evidence that corruption occurred in our runs.

Full-controller restart, a richer verified-state ledger, SlopCodeBench sequential
checkpoint evaluation and a live LongHorizon comparison remain follow-on work.
They must not be described as completed by this protocol.

## Outcome addendum — 2026-10-06

All 28 registered cells have returned, including four solver deadlines. The
full results and mechanism activation counts are in [the result report](mechanism01-progress.md).
Neither fresh-history treatment entered a second executor episode, so this
cohort does not identify the effect of clearing history. One bounded re-audit
actually ran; its first SDK audit was incomplete, and the second accepted an
unchanged artifact. It did not improve the artifact through another executor.

Separately registered controller-loss integration and SCBench transfer work are
tracked in [the current research findings](research-findings-2026-10-06.md).
Their outcomes are not additional independent observations for this four-task
mechanism cohort. A real LongHorizon comparison remains unimplemented.
