#!/usr/bin/env bash
# PRD-to-Plans: bash entrypoint for ``speckit.prd.orchestrate``.
#
# Pure dispatch wrapper around the canonical Python state engine
# (``prd_orchestrate.py``). Forwards every argument verbatim and emits
# the engine's single-line JSON on stdout. Exits with the engine's
# status (0 on success, 1 on rejected action, 2 on argument error).
#
# Usage mirrors the Python twin:
#   prd_orchestrate.sh slug=<slug> action=initialize|status|next|start|evidence|complete|block|reopen|approve

set -euo pipefail

SCRIPT_DIR="$(CDPATH="" cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./prd-common.sh
. "$SCRIPT_DIR/prd-common.sh"

# Locate the Python interpreter in a portable way (matches
# scripts/python/common.py). Prefer SPECKIT_PYTHON; otherwise fall
# back to ``python3`` or ``python``. The script never invokes an
# interpreter directly when invoked by a shell script — the engine
# does all logic. This wrapper only needs to spawn the right binary.
if [[ -n "${SPECKIT_PYTHON:-}" ]]; then
    PYTHON_BIN="$SPECKIT_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    err "ERROR: no Python interpreter on PATH (set SPECKIT_PYTHON or install python3)"
    exit 1
fi

if [[ $# -eq 0 ]]; then
    err "Usage: prd_orchestrate.sh slug=<slug> action=<initialize|status|next|start|evidence|complete|block|reopen|approve> ..."
    exit 2
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/../python/prd_orchestrate.py" "$@"
