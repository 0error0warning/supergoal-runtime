# Result provenance

## Current public studies

The [research findings](../../docs/research-findings-2026-10-06.md) and
[cohort index](../public_benchmarks/README.md) describe the current GCP studies.
Their sources and models are distinct from the earlier synthetic matrix below.

The later [intent/evidence study](../../docs/intent-transfer01-study.md) has three
separate raw capsules: `intent-evidence-dev01-final01` and
`intent-evidence-dev02-final01` retain the six development runs, and
`intent-transfer01-final01` retains all 32 registered transfer trials. Each has a
SHA256 manifest. The final capsule includes source and task registrations,
request journals, original verifier results, anonymous research diagnostics,
failed setup/audit attempts, the successful identity audit, the 251-test real
Hermes validation, and the separately declared 20-run serial timing diagnostic.
Original grades are not replaced by timing results. Seven of eight research
content grades remain invalid; file delivery is never counted as research quality.

`intent-analysis01/manifest.json` pins the descriptive analyses, their source,
regression tests, reproduction checks and final service status. All original
research input bytes were retained; one solver added an extracted-text file,
which is reported separately from changing an original input. Initial launch
receipts record launch-time state; completed cohort receipts describe final state.
Exports exclude private Hermes source, credentials, input corpora, raw full model
traces and Docker layers. Trace hashes and benchmark provenance remain available.
The GCP instance and experimental tunnel were stopped after verified export;
both persistent disks and the existing production Hermes deployment remain.

For `public-parallel24-v1`, `mechanism01` and `harness-transfer01`, the local
`<cohort>-audited-bundle.json` contains the exported outcomes and any explicit
adjudications; `<cohort>-analysis.json` is a regenerated descriptive analysis.
`mechanism01-audited-analysis.json` is an earlier analysis snapshot, not an
additional cohort. A changed observation timestamp does not create new trials.

`public-parallel24-v1-final01/manifest.json` preserves the complete 110-outcome
analysis and final identity/budget audit. `token-usage-accounting01/manifest.json`
preserves a later analysis correction across the three cohorts: requests lacking
complete token receipts make full token totals unknown, while observed subtotals
remain available. Scores, physical request counts, and original records are
unchanged. The corrected canonical analyses exclude incomplete token totals from
paired token comparisons; they retain those trials in outcome/request comparisons.

`context-handoff01-launch/manifest.json` preserves the four-condition live
integration gate, five POSIX boundary checks, registration, source archive, and
launch state. `context-handoff01-analysis.json` now contains all twelve
trajectories and 48 grades. It verifies actual SDK context
fingerprints before counting an intervention as activated. Its three development
problems are not new task IDs, and its checkpoints are not independent samples.
The `20261006-080048` capsule preserves the six returned trajectories, measured
live processes, and frozen read-only finalizer. That finite process waits for the
already running batch, then exports results, budget/source checks, and regressions
without restarting or regrading trials. A waiting finalizer is not evidence that
the batch has completed; its eventual outputs must still be checked.
The later `20261006-081902` capsule contains the complete export and successful
final checks. `context-handoff01-final01/manifest.json` preserves the complete
dataset, registered candidate, analysis sources, and cost/regression supplement.
`context-file-backup-case01/manifest.json` links the observed regression signal to
public requirements and four unchanged official logs. These three development
problems remain the statistical units, and initial-prefix differences limit
causal attribution across the original four complete trajectories.

`context-prefix01-launch/manifest.json` preserves 87 files: the prospective
six-cell suffix registration, 107-file frozen candidate archive, pinned prefix
inputs, all reference and integration attempts, their operators, and the initial
live process evidence. The three original reference solutions passed all nine
suffix checkpoints. `prefix-smoke04` passed both real Hermes/SWE2 input and
artifact checks using 13 physical requests. Earlier oracle/fixture failures all
occurred before model execution and remain recorded. They were constructor,
Docker output, and artificial seed startup issues, not discarded model results.
The formal six-cell study is complete; this launch capsule is its initial state.
The separately frozen finalizer has a measured private network namespace and
performs only evidence export, source/budget audits and analysis after the
registered controller exits. It never retries or regrades a model trial.
`context-prefix01-final01/manifest.json` preserves all six trajectories, 18
grades, test-ID transitions including the reused prefix, 107-source/24-controller
final audit, complete request/token accounting and frozen analysis sources.
The finalizer completed successfully with no input-policy audit errors. Local
analysis exactly reproduces its numerical results. Canonical files are
`context-prefix01-analysis.json` and `../../docs/context-prefix01-progress.md`.
All 18 online passes had strict-test shortfalls; this is a development-sample
diagnostic, not a population false-acceptance estimate.
`checkpoint-cache-maintenance03/manifest.json` records 42 more unused caches
removed after fresh verification of 326 archive blobs. All three common-prefix
images were excluded; zero archive blobs were deleted and no removal was forced.

`lab-research-close01/manifest.json` contains 243 evidence files, including all
240 CAS manifests currently stored (which include engineering probes as well as
model checkpoints). The final new-study audit freshly verified 182 distinct
blobs, 6,818,341,506 bytes, for its three shared prefixes and 18 new checkpoints.
The retention receipt still preserves one historical failed capture alongside
237 durable captures; it is not silently counted as success. A separate stop
receipt records the capture loop's deliberate termination with no in-flight
writes, rather than changing its last running observation.
`lab-service-stop01/manifest.json` confirms the temporary GCP instance reached
TERMINATED with both persistent disks retained, the experimental model tunnel
stopped, and the observed Tencent production services remained active. This
ends the registered run; it does not delete the datasets or promote the local
plugin candidate to production.

`oneday-transfer01-analysis.json` and `oneday-transfer01-receipt.json` now point to
the complete original 18-cell run. The three original parser failures remain
ungraded there. `oneday-transfer01-final-analysis.json` explicitly adds the
separate, eligible three-cell memory repair grading and verifies the original
artifact-manifest hashes. Its request totals cover solving and online auditing,
not the final artifact grader. `oneday-transfer01-final01/manifest.json` preserves
all inputs, the analysis source, and the previous partial canonical files.

`plugin-v2-checks02-evidence/evidence-manifest.json` contains 39 current-source
checks in the pinned real Hermes environment, including two host ABI cases.
These are functional checks with no model calls; they are separate from the
earlier 131-case run and from benchmark quality results. The original network
flag was a requested setting, not a measured namespace; its audit note is kept.

Immutable observations live in dated `research-evidence-*` directories. Each
`manifest.json` records the capture time, remote relative paths, byte sizes and
SHA256 values. The canonical bundle points back to its observation and any
separate adjudication sources. Raw official rewards are not overwritten by
end-to-end failure classifications. Candidate archives and registrations retain
the code and task configuration used before model results were inspected.

The `20261006-0607` evidence contains the zero-model terminal-session repair
probe and cache eviction receipts. Its 21 evicted Docker image caches all had
verified independent archives; no archive blob was deleted. An absent Docker
cache with a durable archive is different from a missing checkpoint. These
engineering probes are not additional benchmark task successes.

The `20261006-075040` observation also includes completed cache maintenance 02:
96 unused Docker caches were evicted after validating their independent archives,
with zero forced removals and zero archive blobs deleted. The same observation
records the live context-study parent and six child processes. It does not certify
that those processes remain live at a later time.

## Earlier synthetic study

The registered task matrix uses `candidate-r3` and
`freeze-candidate-r3-final.json`. `registration-holdout01.json` was written before
any reserved task was executed. The earlier `freeze-candidate-r3.json` is a
superseded draft; it is not the manifest used by the registered matrix.

| Batch | Bundle | Purpose and validity |
|---|---|---|
| dev01 | initial bundle | Infrastructure debugging; 12-second stream-idle cutoff; incorrect v1 startup; generator visible inside namespace. Do not use for efficacy claims. |
| dev02 | candidate-r1 | 90-second stream cutoff; v1 startup still incorrect; generator visible. Apparatus debugging only. |
| dev03 | candidate-r2 | Correct v1 startup; protected v2 control database; generator still visible. Apparatus debugging only. |
| dev04 | candidate-r3 | Sealed-namespace development preflight, three v2 tasks. Development data, not held-out evidence. |
| holdout01a | candidate-r3 | First registered repeat, seeds 7201/7202/7203. |
| holdout01b | candidate-r3 | Second registered repeat, seeds 7203/7202/7201. |

`sg_v1` is the generic-runtime candidate frozen immediately before this study,
not the original public 1.0.0 release. Its archive SHA-256 is recorded in the
manifest. All model calls in the registered study use the same SWE2 alias.

The JSON field `passed` means all configured **hidden benchmark outcome checks**
passed. It does not establish correctness outside those checks. Repeated seeds
and multiple checks per fixture are not additional independent tasks.
The historical registration key `independent_generated_fixtures` counts nine
distinct procedural fixtures; it is not a statistical independence guarantee.
The code and data families share their event-normalization semantics. Final
analysis uses the clearer label `distinct_procedural_fixtures`.

The historical field `false_completion` is operationally a normal terminal stop
whose artifacts fail hidden checks. For plain Hermes and no-verification arms,
an SDK `completed` flag describes the end of a tool loop, not the semantic truth
of the final prose. Therefore describe this field as **unmet task at terminal
stop** in comparisons; do not claim it measures a natural-language false success
assertion without a separate blinded annotation. Controller-level false verified
success can be reported separately for the arms that actually have a verifier.

Reported token totals exclude unavailable usage records; unavailable never means
zero. Physical requests include retries and evaluators. Queuing, actual execution
and staged waiting must not be conflated when reporting duration.

Two staged-input runs exist: `soak-v2-20261005` was an r1 apparatus test;
`soak-v2-r3-20261005` uses the sealed r3 bundle and protected control state. Only
the latter can support the registered candidate's duration claim. Neither tests
two hours of continuous reasoning. The injected supervisor restart occurs after
an acknowledged, persisted waiting boundary; in-flight side-effect recovery and
the host inbox/checkpoint crash windows require separate testing.

`fault-replay.json` is a supplementary, exploratory mechanism probe generated by
`experiments/analysis/fault_replay.py` after the primary freeze. It contains six
paired synthetic artifact/checker conditions, with zero live model calls. It
also retains a counterexample: a weak checker falsely accepts a wrong artifact
even with verification enabled. These decisions are not live task success
rates, and the probe does not estimate the prevalence of the injected faults.

`process-crash-local.json` and `process-crash-server.json` contain eleven paired
process-crash conditions each, on Windows and Linux respectively. Full kernel
and no-transaction control receive the same operations and crash points. These
are deterministic mechanism probes with zero model calls, not additional agent
tasks or a comparison against another durable workflow engine. The first local
probe's cleanup error is retained in `process-crash-apparatus-error.json`.

`supergoal-contract-tests-r3.log` records the real-host full invocation (118 pass,
2 fail). `supergoal-contract-tests-repaired.log` records the targeted rerun after
fixing two tests that assumed the current host's controller registry was a list
rather than a dictionary (2 pass). The frozen study runtime was not changed.

`context-reset01-results.json` retains all six supplemental context-policy runs.
Seed 8602 revealed an incorrect public-checker invariant: the legitimate final
value for id `zero` is 42, while the checker demanded 0. The minimal-context arm
kept correct artifacts but exhausted its budget; the other two arms ended with
runtime success and incorrect artifacts. Do not report its aggregate 3/6 as an
unconfounded comparison of context policy efficacy. Seed 8601's three runs pass.

`context-reset02` is a new six-cell registration on new seeds after removing the
invalid anchor and checking known-correct outputs before model execution. Its
results do not replace study 01. The primary matrix and its frozen task generator
remain unchanged. The original anchor happens to agree with the correct value
for every reserved primary seed, as independently checked during diagnosis.

Final outputs are `holdout01-analysis.json/.md` (108/108 covered, no audit errors),
`context-analysis.json/.md` (both six-cell registrations retained), and the sealed
soak receipt/audit. `holdout01-progress.json/.md` is an earlier half-study snapshot
and is superseded by the complete analysis; it is not an additional dataset.

The primary table counts 791 requests forwarded to the model service. Another
216 local `/api/show` HTTP attempts were rejected by the proxy and never forwarded;
they are recorded separately, not silently counted as zero-token model inference.

The final host audit checks frozen bundle hashes, the accepted soak artifacts,
experimental service termination, and the existing production services. Exact
source archives and the report make the experimental headless integration scope
explicit; no production Gateway upgrade or plugin switch is implied.
