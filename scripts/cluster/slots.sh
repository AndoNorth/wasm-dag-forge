#!/usr/bin/env bash
# Checks the pod slots of a preset: each slot has requests equal to limits, and a slot larger than what a pool node has
# left after its daemons is reported, because the pod would never schedule there. The node allocatable is the node size
# minus the reservation, and the daemon set pods that every worker runs (kindnet) request part of it first.
# Usage: slots.sh <preset>. Exit 0 slots valid and fit, 1 a slot has requests different from limits or does not fit, 2 usage.
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

# Requests of the daemon set pods on a worker node, measured on the kind node image of deploy/versions.yaml.
DAEMON_CPU_MILLI=100
DAEMON_MEM_MIB=50

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
    free_cpu=$((alloc_cpu - DAEMON_CPU_MILLI))
    free_mem=$((alloc_mem - DAEMON_MEM_MIB))
    slot_cpu=$(milli "$(yq ".pools.$pool.limits.cpu" "$slots")")
    slot_mem=$(mib "$(yq ".pools.$pool.limits.memory" "$slots")")
    verdict=fits
    { [ "$slot_cpu" -le "$free_cpu" ] && [ "$slot_mem" -le "$free_mem" ]; } || { verdict="DOES NOT FIT"; status=1; }
    echo "slot $pool: ${slot_cpu}m/${slot_mem}Mi, node allocatable ${alloc_cpu}m/${alloc_mem}Mi, after daemons ${free_cpu}m/${free_mem}Mi, $verdict"
done
exit $status
