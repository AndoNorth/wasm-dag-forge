#!/usr/bin/env bash
# Unit tests: everything not marked integration. pytest exit 5 (nothing collected) is success.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/pytest ]; then pytest=.venv/bin/pytest
elif command -v pytest >/dev/null; then pytest=pytest
else echo "notice: pytest not on PATH and no .venv yet, skipping tests"; exit 0
fi

rc=0
"$pytest" -m "not integration" || rc=$?
if [ "$rc" -eq 5 ]; then echo "notice: no tests collected"; exit 0; fi
exit "$rc"
