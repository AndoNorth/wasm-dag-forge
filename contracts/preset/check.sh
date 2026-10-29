#!/usr/bin/env bash
# Preset loader: validates one preset file or one host ports file and names the reason when it fails.
# Usage: check.sh <file>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$here/../../scripts/verify/cluster/lib/tools.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <preset or ports file>" >&2; exit 2; }
file=$1

ROLES=(control-plane infra obs compile simulate aggregate)
# The reservation rule is written once in README.md: eviction-hard memory is 256 MiB plus a sixteenth of the node
# limit, and the node must keep at least MIN_ALLOCATABLE_MIB allocatable after it.
MIN_ALLOCATABLE_MIB=256

fail() {
    echo "reason: $1: $2" >&2
    exit 1
}

validate() {
    local out
    out=$(check-jsonschema --schemafile "$here/$1" "$file" 2>&1) || fail schema-violation "$(tail -n +2 <<<"$out" | head -n3 | tr '\n' ' ')"
}

if [ "$(yq 'has("ports")' "$file")" = true ]; then
    validate ports.schema.json
    dup=$(yq '.ports[].host_port' "$file" | sort | uniq -d | head -n1)
    [ -z "$dup" ] || fail duplicate-host-port "host port $dup is mapped twice"
    echo "ok $file"
    exit 0
fi

mapfile -t have < <(yq '.nodes[].role' "$file")
for r in "${have[@]}"; do
    [[ " ${ROLES[*]} " == *" $r "* ]] || fail unknown-role "$r is not one of ${ROLES[*]}"
done
for r in "${ROLES[@]}"; do
    [[ " ${have[*]} " == *" $r "* ]] || fail missing-role "no node with role $r"
done

validate preset.schema.json

read -r sum_mem sum_cpus < <(yq '.nodes[] | .count + " " + .cpus + " " + .memory_mib' "$file" |
    awk '{ cpus += $1 * $2; mem += $1 * $3 } END { print mem, cpus }')
want=$(yq '.memory_mib' "$file")
[ "$want" = "$sum_mem" ] || fail memory-sum-mismatch "memory_mib is $want, the nodes sum to $sum_mem"
want=$(yq '.cpus' "$file")
[ "$want" = "$sum_cpus" ] || fail cpus-sum-mismatch "cpus is $want, the nodes sum to $sum_cpus"

while read -r role mem; do
    reserved=$((256 + (mem + 15) / 16))
    [ $((mem - reserved)) -ge "$MIN_ALLOCATABLE_MIB" ] ||
        fail node-memory-below-reservation "$role node has $mem MiB, reservation $reserved MiB leaves less than $MIN_ALLOCATABLE_MIB MiB"
done < <(yq '.nodes[] | .role + " " + .memory_mib' "$file")

echo "ok $file"
