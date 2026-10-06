# Recorded laboratory launch helpers

These are byte-for-byte copies of the two project-owned helper scripts actually
used on the experimental host. They were copied into the repository during the
study; their hashes are in `results/host-environment.json`. They are supplemental
provenance, not newly claimed members of the earlier source-freeze manifest.

`launch.py` opens bind-mount sources as file descriptors before bubblewrap drops
the worker's privileges. It accepts only a trusted supervisor-created argument
file in this deployment. It is not a general privileged API or a setuid program.

`registered_matrix.py` runs the two registered batches against the fixed r3
bundle. The service supplies `SUPERGOAL_STUDY_BUNDLES` pointing at that bundle.
Neither helper contains credentials or customized/private Hermes source.

The laboratory's bubblewrap command, resource limits, isolated home, input
read-only mounts and model proxy are described by the frozen supervisor. Exact
paths and UID/GID are deployment configuration and must be deliberately adapted
for another host, with a new environment record and preflight.
