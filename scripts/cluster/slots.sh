#!/usr/bin/env bash
# Checks the pod slots of a preset: each slot has requests equal to limits, and a slot larger than the allocatable of
# its pool node is reported, because the pod would never schedule there.
# Usage: slots.sh <preset>. Exit 0 slots valid, 1 a slot has requests different from limits, 2 usage.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
# shellcheck source=/dev/null
source scripts/verify/cluster/lib/tools.sh

[ $# -eq 1 ] && [ -f "deploy/presets/slots/$1.yaml" ] || { echo "usage: slots.sh <preset>: no deploy/presets/slots/$1.yaml" >&2; exit 2; }
preset=deploy/presets/$1.yaml
slots=deploy/presets/slots/$1.yaml

# Quantities are whole cores or millicores, and Gi or Mi.
milli() { case $1 in *m) echo "${1%m}" ;; *) awk -v c="$1" 'BEGIN { print c * 1000 }' ;; esac; }
mib() { case $1 in *Gi) echo $((${1%Gi} * 1024)) ;; *Mi) echo "${1%Mi}" ;; *) echo "bad quantity: $1" >&2; return 1 ;; esac; }

status=0
for pool in $(yq '.pools | keys | .[]' "$slots"); do
    for res in cpu memory; do
        req=$(yq ".pools.$pool.requests.$res" "$slots")
        lim=$(yq ".pools.$pool.limits.$res" "$slots")
        [ "$req" = "$lim" ] || { echo "slot $pool: $res request $req differs from limit $lim" >&2; status=1; }
    done
    [ "$status" -eq 0 ] || continue
    read -r node_cpus node_mem < <(yq ".nodes[] | select(.role == \"$pool\") | .cpus + \" \" + .memory_mib" "$preset")
    alloc_cpu=$(($(milli "$node_cpus") - 100))
    alloc_mem=$((node_mem - (256 + (node_mem + 15) / 16)))
    slot_cpu=$(milli "$(yq ".pools.$pool.limits.cpu" "$slots")")
    slot_mem=$(mib "$(yq ".pools.$pool.limits.memory" "$slots")")
    verdict=fits
    { [ "$slot_cpu" -le "$alloc_cpu" ] && [ "$slot_mem" -le "$alloc_mem" ]; } || verdict="DOES NOT FIT"
    echo "slot $pool: ${slot_cpu}m/${slot_mem}Mi, node allocatable ${alloc_cpu}m/${alloc_mem}Mi, $verdict"
done
exit $status
