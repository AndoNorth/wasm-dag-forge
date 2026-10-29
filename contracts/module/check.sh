#!/usr/bin/env bash
# Module loader: validates one deploy/<component>/module.yaml and names the reason when it fails.
# Artifact pins resolve against WDF_VERSIONS, default deploy/versions.yaml.
# Usage: check.sh <file>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$here/../../scripts/verify/cluster/lib/tools.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <module.yaml>" >&2; exit 2; }
file=$1
versions=${WDF_VERSIONS:-$here/../../deploy/versions.yaml}

fail() {
    echo "reason: $1: $2" >&2
    exit 1
}

out=$(check-jsonschema --schemafile "$here/module.schema.json" "$file" 2>&1) ||
    fail schema-violation "$(tail -n +2 <<<"$out" | head -n3 | tr '\n' ' ')"

group=$(yq '.group' "$file")
if [ "$group" != obs ]; then
    dep=$(yq '.depends_on[] | select(test("^obs/"))' "$file" | head -n1)
    [ -z "$dep" ] || fail obs-dependency-from-other-group "$group module depends on $dep"
fi

kind=$(yq '.artifact.kind' "$file")
pin=$(yq '.artifact.pin' "$file")
[ "$(PIN=$pin yq ".${kind}s | has(strenv(PIN))" "$versions")" = true ] ||
    fail unpinned-artifact "$kind $pin is not pinned in $versions"

echo "ok $file"
