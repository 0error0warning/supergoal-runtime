# Supergoal research

This directory contains the versioned experiment protocol, deterministic task
generators, hidden outcome scoring, and the real-Hermes runner. Synthetic fault
tests and live-model results are reported separately. A successful run of a
small generated task is not evidence of reliable day-long autonomous work.

The newer [public benchmark study](public_benchmarks/README.md) uses official
Terminal-Bench tasks and remote Docker isolation, first on grok-bot and now on
the GCP experiment host. Its frozen source,
protocol, results and operator scripts are separate from the original synthetic
study described below. No Docker runs on the local Windows machine.

See [protocol-v1.md](protocol-v1.md). Credentials and raw production data are
excluded. The controller-restart evidence retains two executor records for a
synthetic CSV task, including the bounded messages needed to inspect recovery;
these are not production conversations. Server execution uses a separate Hermes
home and bubblewrap filesystem namespace, a non-root UID, and resource limits.

The benchmark outcome scorer runs outside the agent namespace. Public acceptance
checks describe requirements supplied with the task; hidden scoring must not be
used to steer a run. Development tasks may inform changes. Evaluation tasks and
configuration are frozen before their results are inspected.

Local reliability tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_v2_kernel.py tests/unit/test_research_tasks.py -q
```

`supervisor.py` runs the task matrix on the configured Linux laboratory host.
`worker.py` uses the real Hermes AIAgent and the selected arm; it never imports
the hidden expected outputs. `model_proxy.py` restricts all model traffic to
SWE2 and records physical requests and usage, including evaluator overhead.
`grade_code.py` executes submitted code in a separate network-disabled sandbox;
comparison against expected outputs happens in the supervisor.

`soak.py` is a finite three-delivery experiment with a two-hour schedule and one
deliberately injected supervisor exit. It requires a service with restart on
failure. It resumes from its checkpoint, and final completion ends the service.
The task matrix and soak share a file lock to serialize actual model work.

Server paths and UID/GID in `supervisor.py` are laboratory configuration, not
portable installation defaults. Do not point the worker at a production Hermes
home. Run from a versioned bundle selected by `SUPERGOAL_STUDY_BUNDLES`; retain
the previous bundle and failed attempts when changing an experiment.

`freeze.py --output <new-file.json>` hashes the candidate, protocol and reserved
fixtures before evaluation. `summarize.py <results.json>` produces descriptive
counts; it deliberately does not turn a small pilot into a significance claim.

Exact project-owned baseline/candidate source archives are retained under
[`baselines/`](baselines/README.md). The small actual Linux launch helpers are
under [`host-support/`](host-support/README.md); customized Hermes host code and
credentials are excluded.

Analysis and supplementary probes live under `analysis/`. They were added after
the primary freeze and never change its runtime or scores:

- `registered_results.py` checks registration coverage, task hashes, duplicates,
  model identity and request budgets, then writes descriptive paired results.
- `fault_replay.py` and `process_crash.py` probe explicitly injected mechanism
  failures without model calls.
- `context_reset.py` runs a separately registered live study of three context
  policies across fresh Hermes homes and staged data deliveries.
- `soak_audit.py` checks final script reproduction in a fresh sandbox, terminal
  kernel state, and requests during observed input-wait intervals. Its results
  corroborate the soak separately rather than retroactively changing its score.

The 2026-10-06 expansion uses 24 fixed, new Terminal-Bench IDs (five arms),
six AgentIF-OneDay tasks (three arms), a shared CPU/memory admission pool, and
separate real worker-death/response-stream fault checks. See
[the current study record](../docs/parallel-public-study-2026-10-06.md) and
[the frozen TB protocol](public_benchmarks/PUBLIC-PARALLEL24-V1.md).
Reference-solution failures, unavailable environments, raw judge results and
pre-execution registration amendments remain visible. OneDay uses the official
artifact pipeline with SWE2 replacing Gemini; its scores are labeled adapted.

The [October 6 research findings](../docs/research-findings-2026-10-06.md) also
cover the completed mechanism and SlopCodeBench studies, actual upstream
mini-SWE-agent and LongHorizon comparisons, and verified controller, checkpoint
and terminal-session fixes. Frozen model runs retain their original tool
adapters; later zero-model engineering repairs do not replace their scores.

The earlier batches and October 6 evening continuation are complete. The latter used the
[intent/evidence transfer protocol](public_benchmarks/INTENT-TRANSFER01.md):
two development examples followed by six new terminal and two research tasks,
with four arms. All 32 registered trials are retained in the
[final report](../docs/intent-transfer01-study.md). Research file delivery is
separated from model-judged content diagnostics; seven of eight content grades
remain invalid. The experiment VM and model tunnel are stopped. Start at the
[research index](../docs/research-index.md) for results and code boundaries.
Historical launch scripts are records of the experiment environment; running
them can allocate resources or call models and is not part of local validation.

Validate the committed evidence without contacting any service:

```sh
python scripts/verify_research_archive.py
```

Run that command from the repository root. It checks local capsule and baseline
manifest hashes; it does not rerun models, validate remote Docker archives or
establish task correctness. `.gitattributes` preserves research files byte for
byte so Windows line-ending conversion does not invalidate the receipts.
