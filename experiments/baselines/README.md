# Frozen project source

These small archives preserve the exact project-owned source used for the study,
even if the working tree is later improved. They contain no model credentials,
production chats, or private/customized Hermes source.

- `sg-v1-prestudy.tar.gz`: the pre-study **generic-runtime candidate**, including
  the initial smoke runner. It is not the original public 1.0.0 release. Its
  original archive hash matches the primary freeze manifest.
- `sg-v2-r3.tar.gz`: every source listed in `freeze-candidate-r3-final.json`, plus
  this project's license/package entry files. The archive was assembled later
  from byte-identical frozen sources; its packaging timestamp is not the study's
  preregistration timestamp. Entry contents must match the earlier manifest.
- `sg-public-dev01.tar.gz` through `sg-public-dev04.tar.gz`: the exact source files
  in each public-benchmark candidate's registration, plus that registration.
  Retrieved from the frozen server candidates after verifying every registered
  SHA-256. Archive creation time is not the preregistration time. Operator-only
  launch/collection scripts are recorded separately from the frozen agent.
- `public-dev04-operators.tar.gz` and `public-dev04-reporting.tar.gz`: the operator,
  collector and one-shot report-generation code used for the live public study.
  They are not model-visible agent source and contain no private host code.

Archive hashes are in `archives.json`. Extract only into a new empty directory,
never over an active checkout. The current root `LICENSE` applies to the
project-owned material.

Reproducing the live integration still requires a compatible Hermes host with
the tested command, plugin-hook and turn-controller APIs. The laboratory used
the user's customized v0.21.3 release, not an asserted stock upstream install.
The public scripts disclose this dependency instead of publishing private host
code or silently substituting a simulated agent.
