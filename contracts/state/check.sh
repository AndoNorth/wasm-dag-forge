#!/usr/bin/env bash
# Retention loader: validates retention.yaml against its schema and the retention rules.
# Usage: check.sh <retention.yaml>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <retention.yaml>" >&2; exit 2; }
exec uv run --frozen --quiet --project "$here/../.." python -m wdf_eventstore.retention "$1"
