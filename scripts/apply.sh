#!/usr/bin/env bash
set -euo pipefail
TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "usage: $0 /path/to/hermes-agent" >&2
  exit 2
fi
if [[ ! -d "$TARGET/.git" ]]; then
  echo "target is not a git checkout: $TARGET" >&2
  exit 2
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$SCRIPT_DIR/../patches/supergoal-runtime.patch"
cd "$TARGET"
git apply --check "$PATCH"
git apply "$PATCH"
echo "Applied supergoal runtime patch to $TARGET"
echo "Recommended verification:"
echo "  PYTHONPATH=. pytest tests/hermes_cli/test_goals.py -q"
echo "  PYTHONPATH=. pytest tests/gateway/test_goal_verdict_send.py tests/gateway/test_goal_status_notice.py tests/gateway/test_supergoal_max_turns_config.py tests/hermes_cli/test_supergoal_command_registry.py -q"
