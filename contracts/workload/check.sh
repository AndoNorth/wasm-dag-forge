#!/usr/bin/env bash
# Workload contract loader: validates one contract file, vector, results.xml or guest stderr file.
# Usage: check.sh <file>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <file>" >&2; exit 2; }
exec uv run --frozen --quiet --project "$here/../.." python -m wdf_runtime.contract --check "$1"
