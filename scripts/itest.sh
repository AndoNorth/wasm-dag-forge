#!/usr/bin/env bash
# Integration tests, optionally limited to packages/<domain>. pytest exit 5 (nothing collected) is success.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "error: uv not on PATH, run inside nix develop" >&2; exit 1; }
# --frozen runs the pytest pinned in uv.lock, and uv fails loudly when it cannot provide one.
pytest=(uv run --frozen pytest)

target=()
[ -n "${1:-}" ] && target=("packages/$1")

rc=0
"${pytest[@]}" -m integration "${target[@]}" || rc=$?
if [ "$rc" -eq 5 ]; then echo "notice: no integration tests collected"; exit 0; fi
exit "$rc"
