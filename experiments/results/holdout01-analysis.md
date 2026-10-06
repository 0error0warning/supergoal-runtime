# Registered pilot results

Status: complete; 108/108 runs.

| Arm | Outcome pass | Unmet terminal stop | Multi-episode | Physical requests | Known tokens | Missing usage | Median wall s |
|---|---:|---:|---:|---:|---:|---:|---:|
| hermes | 18/18 | 0 | 0 | 126 | 802,594 | 0 | 53.1 |
| native_goal | 18/18 | 0 | 0 | 138 | 788,225 | 0 | 48.5 |
| sg_v1 | 18/18 | 0 | 0 | 158 | 835,485 | 0 | 74.5 |
| sg_v2 | 18/18 | 0 | 0 | 130 | 861,170 | 0 | 65.3 |
| sg_v2_no_context | 18/18 | 0 | 0 | 117 | 720,413 | 0 | 58.9 |
| sg_v2_no_verification | 18/18 | 0 | 0 | 122 | 772,288 | 0 | 54.0 |

The table counts requests forwarded to the model provider, including judges and retries.
Local rejected HTTP attempts are reported separately: 216.
These local rejections were not forwarded and are not assigned model token usage.
Token totals are provider-reported
input plus output usage, including cached input; they are not an invoice or unique-text count.
Wall time includes queueing, environment startup, checks and SDK work.

## Paired outcomes

| V2 compared with | Pairs | V2 only passes | Other only passes | Both pass | Neither passes |
|---|---:|---:|---:|---:|---:|
| hermes | 18 | 0 | 0 | 18 | 0 |
| native_goal | 18 | 0 | 0 | 18 | 0 |
| sg_v1 | 18 | 0 | 0 | 18 | 0 |
| sg_v2_no_context | 18 | 0 | 0 | 18 | 0 |
| sg_v2_no_verification | 18 | 0 | 0 | 18 | 0 |

- Descriptive pilot; no confirmatory significance test or generality claim.
- Nine related procedural fixtures, repeated twice; not 108 independent tasks.
- Synthesis prose quality and unseen data generalization are not fully graded.
- A missing usage record contributes no known tokens and must be reported separately.
- Model alias is not an immutable checkpoint; billing is not verified.
- One-episode runs do not exercise task-state context reinforcement.
