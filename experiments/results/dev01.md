| Arm | Artifact pass | Declared complete | False completion | Runs with errors | Physical calls | Seconds |
|---|---:|---:|---:|---:|---:|---:|
| hermes | 1/1 | 0 | 0 | 1 | 10 | 155.0 |
| native_goal | 1/1 | 1 | 0 | 0 | 10 | 103.6 |
| sg_v1 | 1/1 | 0 | 0 | 1 | 6 | 90.2 |
| sg_v2 | 1/1 | 1 | 0 | 1 | 6 | 98.2 |
| sg_v2_no_context | 1/1 | 1 | 0 | 0 | 8 | 97.2 |
| sg_v2_no_verification | 1/1 | 1 | 0 | 1 | 12 | 110.3 |

Counts describe these fixtures only. Different seeds within a family are related tasks,
and repeated samples must not be counted as independent evidence of generality.

The synthesis score covers structured facts and brief presence, not the prose quality
of the brief. The data score includes rerunning the script on the original inputs,
not generalization to unseen datasets. These limitations accompany completion claims.
