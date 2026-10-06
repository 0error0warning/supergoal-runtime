# Fresh-session context-policy studies

Study 01 is retained in full. Its seed 8602 has an incorrect public checker
and cannot support an unconfounded efficacy comparison. Study 02 uses new
fixtures after a recorded correction and known-correct-output preflight.

## context-reset01

| Fixture | Policy | All phases correct and stopped | Final artifact correct | Runtime status | Episodes | Requests |
|---|---|---:|---:|---|---:|---:|
| 8601 | minimal_continue | True | True | succeeded | 3 | 16 |
| 8601 | durable_state | True | True | succeeded | 3 | 13 |
| 8601 | original_goal | True | True | succeeded | 3 | 13 |
| 8602 (invalid checker) | minimal_continue | False | True | budget_exhausted | 6 | 31 |
| 8602 (invalid checker) | durable_state | False | False | succeeded | 3 | 19 |
| 8602 (invalid checker) | original_goal | False | False | succeeded | 3 | 15 |

Descriptive totals include the invalid fixture; do not infer policy efficacy.

| Policy | Physical requests | Known total tokens | Missing usage records |
|---|---:|---:|---:|
| minimal_continue | 47 | 276,369 | 0 |
| original_goal | 28 | 186,372 | 0 |
| durable_state | 32 | 199,083 | 0 |

No superiority/equivalence claim: few related fixtures, a single model alias,
one sample per condition, shared host/provider, and no long-duration reasoning.

## context-reset02

| Fixture | Policy | All phases correct and stopped | Final artifact correct | Runtime status | Episodes | Requests |
|---|---|---:|---:|---|---:|---:|
| 8701 | minimal_continue | True | True | succeeded | 3 | 19 |
| 8701 | durable_state | True | True | succeeded | 3 | 13 |
| 8701 | original_goal | True | True | succeeded | 3 | 13 |
| 8702 | minimal_continue | True | True | succeeded | 3 | 14 |
| 8702 | durable_state | True | True | succeeded | 3 | 14 |
| 8702 | original_goal | True | True | succeeded | 3 | 13 |

Descriptive totals over two new related fixtures:

| Policy | Physical requests | Known total tokens | Missing usage records |
|---|---:|---:|---:|
| minimal_continue | 33 | 200,462 | 0 |
| original_goal | 26 | 145,079 | 0 |
| durable_state | 27 | 156,502 | 0 |

No superiority/equivalence claim: few related fixtures, a single model alias,
one sample per condition, shared host/provider, and no long-duration reasoning.
