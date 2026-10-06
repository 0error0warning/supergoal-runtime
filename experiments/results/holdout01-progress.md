# Registered pilot results

Status: INCOMPLETE OR AUDIT FAILED; 54/108 runs.

| Arm | Outcome pass | Unmet terminal stop | Multi-episode | Physical requests | Known tokens | Missing usage | Median wall s |
|---|---:|---:|---:|---:|---:|---:|---:|
| hermes | 9/9 | 0 | 0 | 63 | 405,072 | 0 | 40.8 |
| native_goal | 9/9 | 0 | 0 | 69 | 382,826 | 0 | 41.4 |
| sg_v1 | 9/9 | 0 | 0 | 80 | 427,935 | 0 | 56.6 |
| sg_v2 | 9/9 | 0 | 0 | 59 | 371,563 | 0 | 46.2 |
| sg_v2_no_context | 9/9 | 0 | 0 | 61 | 371,107 | 0 | 57.7 |
| sg_v2_no_verification | 9/9 | 0 | 0 | 61 | 382,943 | 0 | 47.9 |

All requests, including judges and retries, are counted. Token totals are provider-reported
input plus output usage, including cached input; they are not an invoice or unique-text count.
Wall time includes queueing, environment startup, checks and SDK work.

## Paired outcomes

| V2 compared with | Pairs | V2 only passes | Other only passes | Both pass | Neither passes |
|---|---:|---:|---:|---:|---:|
| hermes | 9 | 0 | 0 | 9 | 0 |
| native_goal | 9 | 0 | 0 | 9 | 0 |
| sg_v1 | 9 | 0 | 0 | 9 | 0 |
| sg_v2_no_context | 9 | 0 | 0 | 9 | 0 |
| sg_v2_no_verification | 9 | 0 | 0 | 9 | 0 |

- Descriptive pilot; no confirmatory significance test or generality claim.
- Nine related procedural fixtures, repeated twice; not 108 independent tasks.
- Synthesis prose quality and unseen data generalization are not fully graded.
- A missing usage record contributes no known tokens and must be reported separately.
- Model alias is not an immutable checkpoint; billing is not verified.
- One-episode runs do not exercise task-state context reinforcement.
