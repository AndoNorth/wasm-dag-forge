#!/usr/bin/env bash
# Compares every node's allocatable memory and cpu with the node size in a rendered limits.yaml minus the reservation.
# Expected memory is the node size minus the eviction threshold of contracts/preset/README.md, and expected cpu is the
# node size minus the system reservation that render.sh applies. Prints expected and actual per node.
# Usage: allocatable.sh <limits.yaml>. Exit 0 all equal, 1 a node differs, 2 usage.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
# shellcheck source=/dev/null
source scripts/verify/cluster/lib/tools.sh

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: allocatable.sh <limits.yaml>" >&2; exit 2; }
context=${WDF_CONTEXT:-kind-wdf}
cpu_reserved_milli=100

# The API prints a quantity as Ki, Mi, Gi or plain bytes depending on divisibility.
to_kib() {
    case $1 in
        *Ki) echo "${1%Ki}" ;;
        *Mi) echo $((${1%Mi} * 1024)) ;;
        *Gi) echo $((${1%Gi} * 1024 * 1024)) ;;
        *) echo $(($1 / 1024)) ;;
    esac
}

status=0
printf '%-20s %-26s %-26s %-18s %s\n' node memory-expected-MiB memory-actual-MiB cpu-expected-actual result
while read -r node cpus mem; do
    want_mem=$((mem - (256 + (mem + 15) / 16)))
    want_cpu=$(awk -v c="$cpus" -v r="$cpu_reserved_milli" 'BEGIN { print c * 1000 - r }')
    alloc=$(kubectl --context "$context" get node "$node" -o jsonpath='{.status.allocatable.memory} {.status.allocatable.cpu}')
    read -r got_mem got_cpu <<<"$alloc"
    got_mem_kib=$(to_kib "$got_mem")
    got_cpu_milli=$([[ $got_cpu == *m ]] && echo "${got_cpu%m}" || echo $((got_cpu * 1000)))
    result=ok
    [ "$got_mem_kib" -eq $((want_mem * 1024)) ] && [ "$got_cpu_milli" -eq "$want_cpu" ] || { result=MISMATCH; status=1; }
    printf '%-20s %-26s %-26s %-18s %s\n' "$node" "$want_mem" "$((got_mem_kib / 1024))" "${want_cpu}m/${got_cpu_milli}m" "$result"
done < <(yq '.nodes[] | .container + " " + .cpus + " " + .memory_mib' "$1")
exit $status
