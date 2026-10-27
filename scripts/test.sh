#!/usr/bin/env bash
# Unit tests: everything not marked integration. pytest exit 5 (nothing collected) is success.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null || { echo "error: uv not on PATH, run inside nix develop" >&2; exit 1; }
# --frozen runs the pytest pinned in uv.lock, and uv fails loudly when it cannot provide one.
pytest=(uv run --frozen pytest)

rc=0
"${pytest[@]}" -m "not integration" || rc=$?
if [ "$rc" -eq 5 ]; then echo "notice: no tests collected"; exit 0; fi
exit "$rc"
