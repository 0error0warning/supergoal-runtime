# Public GCP 01

Registered before the new host's reference-solution checks and any model trial.
This is an exploratory, paired comparison on six public Terminal-Bench 2.1 hard
tasks from six categories, using Hermes 0.21.3 and Devin SWE2. Each task has one
trial for each of `native`, `sg_v2`, `repeat_goal`, `sg_no_context`, and
`sg_no_review`; there are no additional stochastic repeats.

The frozen runtime and adapter are byte-identical to public-dev04. The experiment
label identifies the new task cohort and GCP environment, not a runtime upgrade.
The old cohort's scores and controller failure remain part of the research
record. In particular, StaleLease handling and destructive workspace recovery
have not been improved in this candidate.

The task list, rotated arm order, budgets, source hashes, prior task exposure,
failure policy, endpoints and limitations are in
[registration-public-gcp01.json](registration-public-gcp01.json). All six tasks
have official hard difficulty, one CPU, 2 GiB memory, and a 900- or 1800-second
agent limit. Every arm shares a cap of 96 physical model requests, including
review requests, and at most 6000 output tokens per request. Official task
instructions, solutions and graders are unchanged. The existing Debian APT
HTTPS transport adaptation is applied identically to all arms and reference
solutions.

Reference solutions must pass on the new host before model trials start. Record
task-tree hashes, immutable image IDs/digests, Python dependencies and the host
configuration in a separate environment lock. The final grader runs only after
the solver; the solver cannot read the host control state or final grader files.
Public network access means benchmark lookup remains a possible contamination
route, so trajectories must be audited before interpreting the scores.

The primary comparison is official task completion, with started controller or
runtime failures also included as failures in the end-to-end rate. An apparatus
problem retains its raw record and receives an explicit diagnosis. Never retry a
started row automatically, erase a failure, or silently replace a difficult task.
Not-started rows remain pending rather than being scored as zero. Six paired
tasks support descriptive comparisons only. These short bounded tasks do not
prove autonomous work lasting hours or days; the separate long-horizon phase
still needs to be run.

The GCP job is a finite server-side service and does not require the Windows
machine to remain online. Its cloud auto-stop deadline is 2026-10-06 14:24:06 UTC.
The VM's hourly cost continues until stopped; retained disks remain billable.
