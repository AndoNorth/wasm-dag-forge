#!/usr/bin/env bash
# Loader test for one contract directory: every valid vector must pass its check.sh and every invalid vector must
# fail it with the reason named by its directory. Extra arguments are real files that must also pass.
# Usage: vectors.sh <contract dir> [real file...]
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$here/tools.sh"

[ $# -ge 1 ] || { echo "usage: vectors.sh <contract dir> [real file...]" >&2; exit 2; }
dir=${1%/}
shift
check="$dir/check.sh"
name=$(basename "$dir")
failed=0
n=0

version=$(cat "$dir/VERSION")
[[ $version =~ ^[0-9]+$ ]] || { echo "FAIL $name: VERSION is not an integer" >&2; failed=1; }
grep -qx "Version: $version" "$dir/README.md" || { echo "FAIL $name: README.md lacks 'Version: $version'" >&2; failed=1; }

# A contract whose files resolve pins can ship vectors/versions.yaml, which stands in for deploy/versions.yaml for
# the vectors only, so real files below still resolve against the real pins.
vector_check() {
    if [ -f "$dir/vectors/versions.yaml" ]; then WDF_VERSIONS="$dir/vectors/versions.yaml" "$check" "$1"; else "$check" "$1"; fi
}

expect_valid() {
    local run=$1
    shift
    n=$((n + 1))
    if out=$("$run" "$1" 2>&1); then echo "ok   valid   $1"; else echo "FAIL valid   $1: $out" >&2; failed=1; fi
}

for f in "$dir"/vectors/valid/*.yaml; do expect_valid vector_check "$f"; done
for f in "$@"; do expect_valid "$check" "$f"; done

for f in "$dir"/vectors/invalid/*/*.yaml; do
    n=$((n + 1))
    reason=$(basename "$(dirname "$f")")
    rc=0
    out=$(vector_check "$f" 2>&1) || rc=$?
    if [ "$rc" -eq 1 ] && grep -q "^reason: $reason:" <<<"$out"; then
        echo "ok   invalid $f -> $(grep '^reason:' <<<"$out" | head -n1)"
    else
        echo "FAIL invalid $f: want exit 1 with reason $reason, got exit $rc: $out" >&2
        failed=1
    fi
done

echo "$name: $n vectors, $([ "$failed" -eq 0 ] && echo passed || echo FAILED)"
exit "$failed"
