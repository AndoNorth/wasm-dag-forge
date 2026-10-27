#!/usr/bin/env bash
# Integration tests, optionally limited to packages/<domain>. pytest exit 5 (nothing collected) is success.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/bin/pytest ]; then pytest=.venv/bin/pytest
elif command -v pytest >/dev/null; then pytest=pytest
else echo "notice: pytest not on PATH and no .venv yet, skipping integration tests"; exit 0
fi

target=()
[ -n "${1:-}" ] && target=("packages/$1")

rc=0
"$pytest" -m integration "${target[@]}" || rc=$?
if [ "$rc" -eq 5 ]; then echo "notice: no integration tests collected"; exit 0; fi
exit "$rc"
