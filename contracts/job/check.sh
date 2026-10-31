#!/usr/bin/env bash
# Job validator: runs every pass over one document with the ci profile and the live slots files.
# Usage: check.sh <document.json>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
# Exit 3 with "not implemented: owned by lane V" while a pass is still a stub.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <document.json>" >&2; exit 2; }
exec uv run --frozen --quiet --project "$here/../.." python -m wdf_jobcheck.check "$1"
