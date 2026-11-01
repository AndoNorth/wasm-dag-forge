#!/usr/bin/env bash
# Store contract loader: validates roles.yaml, lifecycle.yaml or products.yaml against the models and the bucket layout.
# Usage: check.sh <file>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <roles.yaml|lifecycle.yaml|products.yaml>" >&2; exit 2; }
exec uv run --frozen --quiet --project "$here/../.." python -m wdf_storecheck.contract "$1"
