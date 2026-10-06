# Public benchmark study

The completed research ran real Hermes 0.21.3 and `devin/swe-2` on the remote
GCP host `supergoal-gcp`. Earlier `public-dev04` work used `grok-bot`; its
candidate and results remain historical records. No Docker containers run on
the Windows development machine. Model access used a direct server tunnel,
independent of the development machine. After all registered work and exports
finished, the temporary instance and experiment tunnel were stopped. Persistent
disks and results are retained; a future study must check its budget, deadline
and connection before starting a separately registered run.

Start with the [October 6 findings](../../docs/research-findings-2026-10-06.md),
which distinguish task results, transport failures, adapter defects and repairs.

| Cohort | Registered task units and arms | Results |
|---|---|---|
| public-gcp01 | 6 Terminal-Bench tasks × 5 arms; complete | [Results](../../docs/public-gcp01-study.md) |
| public-parallel24-v1 | 24 tasks × 5 arms; 110 model outcomes complete, 10 cells unavailable across 2 environments | [Final results](../../docs/public-parallel24-v1-progress.md) |
| mechanism01 | 4 development tasks × 7 arms; complete | [Ablations and activation](../../docs/mechanism01-progress.md) |
| oneday-transfer01 | 6 AgentIF-OneDay tasks × 3 arms; original artifacts scored | [Scores and grader-memory amendment](../../docs/research-findings-2026-10-06.md) |
| scb-transfer01 | 3 multi-stage problems × 3 arms; 9 trajectories and 36 checkpoint grades complete | [Results](../../docs/scb-transfer01-progress.md) |
| harness-transfer01 | 3 new tasks × Hermes, SG, upstream mini and upstream LongHorizon; all 12 complete | [Protocol](../../docs/harness-transfer-study-2026-10-06.md), [results](../../docs/harness-transfer01-progress.md) |
| context-handoff01 | 3 previously observed multi-stage problems × actual history carry/reset × public-requirement replay/omission; 12 trajectories and 48 grades complete | [Outcomes and measured activation](../../docs/context-handoff01-progress.md), [cost and regressions](../../docs/context-handoff01-details.md) |
| context-prefix01 | 3 saved common prefixes × replay/current-only; all 6 suffix trajectories and 18 grades complete, 403 new requests | [Protocol](../../docs/context-prefix-study-2026-10-06.md), [paired results](../../docs/context-prefix01-progress.md), [final evidence](../results/context-prefix01-final01/manifest.json) |

Task IDs overlap between some development cohorts. Calls, checkpoints and arms
are not independent tasks, and different frozen candidates are not pooled as a
single randomized experiment. The local working tree includes later repairs;
it must not be substituted into an already running candidate.

## Earlier six-task protocol

Six different Terminal-Bench 2.1 task IDs cover five categories, including two
upstream `hard` tasks. The exact official source commit, task files, images,
budgets and five comparison arms are frozen before model trials. All six
official reference solutions passed after the disclosed Debian HTTPS transport
repair. Reference-solution results validate the apparatus, not the agent.

- [Registration and execution order](registration-public-dev04.json)
- [Task tree and image lock](environment-public-dev04.json)
- [Results and limits](../../docs/public-benchmark-study-2026-10-05.md)
- [Observed failures and next mechanism to test](../../docs/public-benchmark-findings-2026-10-05.md)
- [General research protocol](PROTOCOL-DRAFT.md)
- [Server sizing and isolation](../../docs/benchmark-server-requirements.md)

`hermes_bridge.py` attaches the original Hermes terminal/file tools to one Harbor
container. `harbor_agent.py` implements the registered comparison policies and
uses the actual v2 kernel. All helper reviews and retries share the model request
and wall-time budget. The independent official verifier runs only afterward.

`run_registered.py` is a finite, serial operator runner. It verifies source and
image locks, checks host memory/disk reserves, refuses to overwrite a started
row, and stops for audit on grading or setup problems. It runs on Linux, outside
the task container. Use the frozen candidate, not the mutable working tree.
The historical serial invocation below illustrates `public-dev04`. Current
parallel studies have separately frozen operators and registrations; do not
use this command to relaunch their existing rows:

```bash
python3 /var/lib/supergoal-lab/study-operator/run_registered.py \
  --candidate /var/lib/supergoal-lab/candidates/public-dev04 \
  --registration /var/lib/supergoal-lab/candidates/public-dev04/experiments/public_benchmarks/registration-public-dev04.json \
  --environment-lock /var/lib/supergoal-lab/setup/environment-public-dev04.json \
  --start START_INDEX --stop EXCLUSIVE_STOP_INDEX
```

Do not rerun an existing interval or edit its candidate to improve its result.
Original failures remain recorded; apparatus repairs need a separately identified
run or zero-model-call regrade of retained artifacts. `collect_public.py` exports
allowlisted receipts and official logs; `analyze_public.py` reports task-level
paired results without counting requests or extra seeds as new tasks.

The earlier development candidates remain archived. public-dev01 produced one
model trial whose grader failed to download dependencies. public-dev02/03 were
stopped during zero-model-call apparatus preflights. Neither those failures nor
reference solutions count as model outcomes for public-dev04. Full model traces
and pre-verifier filesystem snapshots stay on the experiment host, with hashes
in the exported receipts.

The one-shot `finalize_registered.py` process exports a complete or partial report
when the already-running batch finishes or needs audit. It neither retries a
model trial nor schedules recurring work. Frozen reporting/operator code is
archived separately under `baselines/`; the local report is a timestamped copy,
while the final server export can finish after the development PC disconnects.

These development tasks do not establish broad superiority or day-long autonomy.
The online reviewer has not been independently calibrated, and a filesystem
snapshot does not preserve running services. The headless research adapter also
does not substitute for an end-to-end study of the production gateway plugin.
