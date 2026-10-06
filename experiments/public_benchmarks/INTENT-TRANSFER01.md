# Intent and evidence policy transfer study

Drafted 2026-10-06 before held-out solver calls. The machine-readable registration
is the authority for final source hashes, admission times and available rows.

## Question and intervention

Can a general work policy improve task completion without treating repeated
activity as progress? The candidate preserves the original request, compiles an
advisory requirement list with exact source quotations, selects direct/local
investigation/research work, and audits each requirement against observations of
the current artifact. Assumptions never authorize actions or change acceptance.

Malformed interpretation/audit objects get a bounded format repair. Unknown
behavior is distinguished from an unavailable observation surface or user-only
information. Only explicitly retryable uncertainty may schedule more execution.
The filesystem snapshot does not preserve running services, memory or volumes;
unobservable behavior must not become a successful audit or an instruction to
repeatedly rebuild an already-present implementation.

## Separation and selection

`configure-git-webserver` and DR3-Eval English task `015` are development examples.
Development failures and all revisions are retained. They are excluded from
transfer estimates. No code branch uses a task ID, task keyword, expected answer,
benchmark category or hidden test result.

Terminal task selection uses only official metadata. Exclude all previously
registered task IDs, GPUs, more than 2 CPUs or 4096 MiB, missing prebuilt images,
solver limits above 1800 seconds or verifier limits above 1200 seconds. Rank
categories and task names by SHA-256 with seed `intent-transfer01:2026-10-06`;
take one task per category, at most six. The resulting tasks are
`merge-diff-arc-agi-task`, `crack-7z-hash`, `sqlite-with-gcov`, `fix-git`,
`pytorch-model-cli`, and `largest-eigenval`. The easy `fix-git` task is an overhead
guard, not a different algorithm or lower-budget condition.

DR3-Eval selection similarly ranks English tasks whose source attachments are
text/PDF formats supported by the common tools, excluding development ID 015.
The two held-out IDs are 014 and 024. Dataset revision is
`4305f9129529d4510f485af6c997b69e1e85b88d`. All arms receive the same original
question, user files and 128k fixed corpus through terminal/file tools. Corpus
task labels and keyword annotations are removed from solver inputs; titles,
URLs and document bodies remain. This is a text/PDF adaptation, not the official
multimodal/RAG benchmark setup or an open-web research test.

## Comparisons

Four arms: one native Hermes episode; six simple `repeat_goal` episodes at most;
the previous Supergoal model-review policy (`sg_v2`); and `evidence` with the new
interpretation/audit policy. SWE2, Hermes source, task tools, images and resource
limits are common. Every interpretation, repair, execution and audit request is
charged to the same 128-request ceiling. Original solver time limits are retained;
research tasks use 900 seconds. The global cloud cutoff is separately enforced.
Equal ceilings do not imply equal actual compute; report actual requests, tokens,
elapsed time and the role breakdown.

One run per task/arm. Dispatch order rotates arms across tasks. No automatic
model-trial retries. A reference environment failure removes that task from all
paired comparisons, with the reason retained. Execution exceptions remain part
of end-to-end outcomes. Admission closes early enough to preserve the full
registered solver and grader interval, rather than silently shortening a row.

## Outcomes and boundaries

Terminal primary outcome: independent official hidden reward, run after the
solver. Secondary: online false acceptance/rejection, unnecessary extra episodes,
evidence coverage, observed routing, actual model use and tool failures. Analyze
paired tasks, not individual model calls. Six tasks and one sample per cell
cannot establish broad statistical superiority or immunity to prompt variation.

Research delivery is recorded under `delivery_present`, **never** called research
success. Research quality is assessed separately using a rubric frozen before
held-out outputs and an arm-blinded source audit. These are model-judged
diagnostics with raw decisions and missing/invalid grades retained, not official
DR3-Eval scores or expert validation. Do not combine them with terminal rewards
into one headline success rate.

This study does not measure private preference recovery through interactive user
simulation, arbitrary web-retrieval quality, or uninterrupted multi-day work.
The long-goal claim still requires those tests. A useful improvement must be tied
to independent outcomes or a verified control failure, not a more elaborate
prompt, more source citations, or a larger token count.

## Sources informing the design

- [Drift-Bench++](https://arxiv.org/abs/2609.38604): distinguishes recovering intent
  from guessing it, and measures inquiry costs and stale intent separately.
- [Asuka-Bench](https://arxiv.org/abs/2606.05920): separates requested behavior and
  observed behavior, and studies refinement from concrete feedback. Its GUI
  evaluator and hidden product requirements are not reproduced here.
- [Anthropic's long-running development harness](https://www.anthropic.com/engineering/harness-design-long-running-apps):
  planner/evaluator value depends on model capability and task difficulty;
  additional phases need an overhead control.
- [DR3-Eval implementation](https://github.com/NJU-LINK/DR3-Eval): fixed corpora
  allow common evidence access. Its evaluator can generate missing gold insights;
  such LLM-derived references must not be presented as deterministic ground truth.
- [Deep Research, Shallow Evaluation](https://arxiv.org/abs/2603.06942): overall
  preference agreement does not validate every metric or individual report.
