#!/usr/bin/env bash
# Stage loader: validates one stage table against the event catalogue and names the reason when it fails.
# Usage: check.sh <stages.yaml>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <stages.yaml>" >&2; exit 2; }
exec uv run --frozen --quiet --project "$here/../.." python -m wdf_stages.check "$1"
