# Context reset study 02: acceptance correction

This is a new exploratory study after a benchmark error was observed in study
01. Keep all study-01 rows, including exhausted budgets and incorrect stops.
Do not merge the new rows into the primary 108-run study or relabel them as
replacement successes for study 01.

The old data acceptance required `{'id':'zero','value':0}`. Generator updates can
legitimately change that record: fixture 8602's correct final value is 42. The
first affected agent produced correct outputs at all six measured boundaries,
yet exhausted its budget because the public checker kept rejecting the answer.
That fixture is not a valid efficacy comparison of context policies. Its raw
behavior remains useful evidence of an erroneous verifier causing wasted work.

Correction: remove the invalid fixed-value anchor; retain the output schema,
ordering, uniqueness, integer-value and reproduction-file checks. Hidden exact
output scoring and clean script reproduction remain unchanged. This fixes a
false negative; it does not make the public checker a complete semantic oracle.

Before any new model call, run a known-correct final output through the public
checker for the regression seed 8602 and new seeds 8701/8702. Record exit codes,
output/checker hashes and fixture identity. This is oracle-based validation of
benchmark construction outside the executor, not feedback to the model. The
executor still receives no hidden expected output or future shard.

Run the same three policies, budgets, fresh-home reset, per-phase hidden scoring
and order seed 50105 on 8701/8702. Register all six cells and script/task hashes
before running. Preserve the frozen primary runtime, generator and study-01
driver. The corrected driver is an explicit separate version, `context_reset02.py`.

Only two related fixtures are involved. Report descriptive results and resource
usage; do not claim general superiority, equivalence, or a long-duration effect.
