#!/usr/bin/env bash
# Topology loader: validates one topology table and checks it covers every pool-bearing preset role.
# Usage: check.sh <file>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$here/../../scripts/verify/cluster/lib/tools.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <topology.yaml>" >&2; exit 2; }
file=$1

fail() {
    echo "reason: $1: $2" >&2
    exit 1
}

out=$(check-jsonschema --schemafile "$here/topology.schema.json" "$file" 2>&1) ||
    fail schema-violation "$(tail -n +2 <<<"$out" | head -n3 | tr '\n' ' ')"

# The preset schema is the one list of roles, and every role except the control plane needs a pool.
mapfile -t roles < <(yq -p json '.properties.nodes.items.properties.role.enum[] | select(. != "control-plane")' \
    "$here/../preset/preset.schema.json")
for r in "${roles[@]}"; do
    [ "$(ROLE=$r yq '.pools | has(strenv(ROLE))' "$file")" = true ] || fail preset-role-without-pool "role $r has no pool"
done
[ "$(yq '.pools | length' "$file")" -eq "${#roles[@]}" ] || fail pool-for-unknown-role "pools has a key that is not a preset role"

echo "ok $file"
