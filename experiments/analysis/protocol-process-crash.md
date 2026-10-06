# Supplementary process-crash probe

Written on 2026-10-05 before executing `process_crash.py`. This is an exploratory
mechanism experiment added after the primary task matrix was frozen. It neither
changes that matrix nor adds model-task outcomes to its denominator.

Question: do interrupted state transitions leave the frozen candidate's database
at a valid transaction boundary, and are committed results safe to acknowledge
again? The paired control removes the multi-statement SQL transaction while
keeping the same schema, operation, statement order, WAL mode and FULL durability
setting. This intentionally weak control isolates atomicity; it is not a claim
to outperform another durable workflow system.

Execute all eleven points in the script, once per arm on local Windows and once
on the Linux experimental host. Terminate a subprocess with `os._exit(86)` after
selected writes but before normal cleanup, or immediately after commit but before
an acknowledgement. Use a separate temporary database for every condition.

Compare recovered rows with an independent before-operation snapshot and a
successful-operation reference. A partial match is a failure of atomicity. For
the full kernel, also check duplicate committed finish, duplicate wake, and
quarantine after a claimed job loses its executor. Report every row, including
failures. No statistical significance claim is appropriate for deterministic
crash locations. Repeating platforms is an environment check, not independent
task sampling.

The probe does not simulate hardware power loss, filesystem corruption, arbitrary
instruction locations, or external side effects. In particular, safe quarantine
means automatic progress stops because the runtime cannot know whether the old
executor caused an effect; it is not proof of autonomous recovery.

Record script/kernel hashes, Python/SQLite versions, OS and process exit codes.
Keep the primary runtime and its freeze manifest unchanged.
