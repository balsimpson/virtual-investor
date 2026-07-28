#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_ROOT="${HERMES_HOME:-${HOME}/.hermes}"
HERMES_PYTHON="${HERMES_PYTHON:-${HERMES_ROOT}/hermes-agent/venv/bin/python}"

if [[ ! -x "$HERMES_PYTHON" ]]; then
  HERMES_PYTHON="$(command -v python3)"
fi

exec "$HERMES_PYTHON" "$SCRIPT_DIR/harper_recovery.py"
