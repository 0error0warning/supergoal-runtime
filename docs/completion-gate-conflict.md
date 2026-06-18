# Supergoal completion/gate conflict after tool-backed success

## Observed signature

A real session (`20260618_054208_1c502e`, AI6 Daily Digest Timeout #2) showed good execution but weak stop control:

- The agent implemented and verified an AI6 intelligence system end-to-end:
  - durable SQLite intelligence DB
  - message/topic/thread/evidence/followup persistence
  - FTS5 + Chinese short-token LIKE fallback
  - evidence backfill for compacted merged topics
  - failure isolation for `intel_persist`
  - real Hermes cron run `dfea9dc3ecd2` refreshed to `last_status=ok`
- The ordinary goal judge repeatedly logged `verdict=done`.
- The supergoal post-turn decision still logged `verdict=continue` because a gate remained open:
  - `completion judge said done, but supergoal gate G2 remains open: Research ledger has sufficient tool-backed external provenance`
- After the production cron E2E had already passed, one extra continuation ran to write an ADR. The ADR was useful, but it was not required for the user's stated “落地，跑通” acceptance criterion.
- Final state became `status=paused continue=False`, not a clean `done`, even though the user-visible response said the supergoal was complete.

## Why this matters

This is not a worker-quality failure. The worker did useful, verified engineering. The control-plane failure is that **completion evidence was not reconciled with gate state**. This can produce:

- unnecessary extra turns after acceptance criteria are met;
- user-visible “done” while internal state is `paused`;
- future `/supergoal resume` risk: the same stale gate can reactivate old work;
- misleading reasons when the text says evidence is sufficient but status treats it as open.

## Diagnostic checklist

1. Inspect session transcript for the real acceptance criteria and last tool-backed verification.
2. Inspect logs for:
   - ordinary `goal judge: verdict=done|continue`;
   - `supergoal post-turn judge verdict`;
   - gate reason strings;
   - continuation enqueue events.
3. If the response claims completion and has strong tool evidence, check whether any open gate is actually a **long-term quality/monitoring gate** rather than a run-completion gate.
4. Watch for semantic inversion in reasons, e.g. `gate G2 remains open: Research ledger has sufficient...` — “sufficient” should normally close a research-sufficiency gate, not keep it open.
5. Distinguish:
   - **Run acceptance gates**: must block completion (`cron last_status=ok`, artifacts exist, evidence missing=0).
   - **Long-term quality gates**: should become followups/monitoring, not block completion (`observe 1-3 days`, thread merge quality over time).

## Fix direction

- Add a reconciliation layer after ordinary done judge:
  - If `completion_judge=done` and the latest verified evidence satisfies the user's acceptance criteria, mark the supergoal `done` even if only monitoring/quality gates remain.
  - Convert residual monitoring gates into followup notes/events instead of continuing the loop.
- Fix G2 semantics:
  - `Research ledger has sufficient tool-backed external provenance` should map to `passed`, or the reason text should be corrected if the underlying status is genuinely open.
- Add tests for the conflict:
  - seed state with an open G2 gate;
  - provide an assistant final response that includes concrete tool-backed run evidence and explicit completion;
  - mock/return ordinary judge `done`;
  - assert supergoal becomes `done` or `done_with_followups`, does **not** enqueue another continuation, and does not end as `paused` solely because a monitoring gate remains.

The important behavior is not the exact enum name; it is that a proven completed mission does not keep looping because a residual research/monitoring gate was not reconciled.
