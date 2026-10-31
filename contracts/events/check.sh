#!/usr/bin/env bash
# Event loader: validates one event file against the CloudEvents schema, the profile and the data schema.
# Usage: check.sh <event.json>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <event.json>" >&2; exit 2; }
exec uv run --frozen --quiet --project "$here/../.." python -m wdf_events.check "$1"
