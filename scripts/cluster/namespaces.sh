#!/usr/bin/env bash
# Creates the topology namespaces with their LimitRanges and one ResourceQuota each, and checks them.
# A quota is the allocatable of the pool nodes its namespace runs on plus 5 percent, so it guards against runaway
# creation while the scheduler's own accounting stays binding. Allocatable is node size minus the reservation rule of
# contracts/preset/README.md and the cpu reservation of render.sh, so the numbers come from the preset and are never typed.
#   apply  create or update the namespaces, LimitRanges and quotas (default)
#   render print the quotas without touching the cluster
#   check  print each quota next to the live pool allocatable, then admit a pod without resources and reject one above the quota
# Usage: namespaces.sh [apply|render|check] <preset>. Exit 0 ok, 1 a check failed, 2 usage.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
# shellcheck source=/dev/null
source scripts/verify/cluster/lib/tools.sh

mode=apply
[ $# -eq 2 ] && { mode=$1; shift; }
[ $# -eq 1 ] && [ -f "deploy/presets/$1.yaml" ] && [[ $mode =~ ^(apply|render|check)$ ]] ||
    { echo "usage: namespaces.sh [apply|render|check] <preset>" >&2; exit 2; }
preset=deploy/presets/$1.yaml
context=${WDF_CONTEXT:-kind-wdf}
k() { kubectl --context "$context" "$@"; }

# The pools whose nodes each namespace runs on. Control pods share the infra pool.
declare -A pools=([wdf-infra]="infra" [wdf-control]="infra" [wdf-workers]="compile simulate aggregate" [wdf-obs]="obs")
namespaces=(wdf-infra wdf-control wdf-workers wdf-obs)
cpu_reserved_milli=100
headroom_percent=5

# Allocatable of the nodes of one pool from the preset, as "<milli> <MiB>".
pool_alloc() {
    yq ".nodes[] | select(.role == \"$1\") | .count + \" \" + .cpus + \" \" + .memory_mib" "$preset" |
        awk -v r="$cpu_reserved_milli" '{ print $1 * ($2 * 1000 - r), $1 * ($3 - (256 + int(($3 + 15) / 16))) }'
}
# Sum of pool_alloc over the pools of a namespace, then the headroom added and rounded up.
quota_for() {
    local p cpu=0 mem=0 c m
    for p in ${pools[$1]}; do
        read -r c m < <(pool_alloc "$p")
        cpu=$((cpu + c)); mem=$((mem + m))
    done
    echo "$(((cpu * (100 + headroom_percent) + 99) / 100)) $(((mem * (100 + headroom_percent) + 99) / 100))"
}

render_quotas() {
    local ns cpu mem
    for ns in "${namespaces[@]}"; do
        read -r cpu mem < <(quota_for "$ns")
        cat <<QUOTA
---
apiVersion: v1
kind: ResourceQuota
metadata:
  name: pool-capacity
  namespace: $ns
spec:
  hard:
    requests.cpu: ${cpu}m
    requests.memory: ${mem}Mi
    limits.cpu: ${cpu}m
    limits.memory: ${mem}Mi
QUOTA
    done
}

to_mib() { case $1 in *Gi) echo $((${1%Gi} * 1024)) ;; *Mi) echo "${1%Mi}" ;; *Ki) echo $((${1%Ki} / 1024)) ;; *) echo $(($1 / 1048576)) ;; esac; }
to_milli() { case $1 in *m) echo "${1%m}" ;; *) echo $(($1 * 1000)) ;; esac; }

# Live allocatable of the nodes in the pools of a namespace, as "<milli> <MiB>".
live_alloc() {
    local pool_label p cpu=0 mem=0 c m
    pool_label=$(yq '.pool_label' contracts/topology/topology.yaml)
    for p in ${pools[$1]}; do
        while read -r c m; do
            cpu=$((cpu + $(to_milli "$c"))); mem=$((mem + $(to_mib "$m")))
        done < <(k get nodes -l "$pool_label=$p" -o jsonpath='{range .items[*]}{.status.allocatable.cpu} {.status.allocatable.memory}{"\n"}{end}')
    done
    echo "$cpu $mem"
}

# A pod manifest for the admission checks, with a memory request and limit of the given size (none when omitted).
probe_pod() { # <namespace> <name> [memory MiB]
    cat <<POD
apiVersion: v1
kind: Pod
metadata:
  name: $2
  namespace: $1
spec:
  restartPolicy: Never
  containers:
    - name: probe
      image: localhost:5001/canary:pinned
      command: ["sleep", "3600"]
$([ -z "${3:-}" ] || printf '      resources:\n        requests:\n          memory: %sMi\n        limits:\n          memory: %sMi\n' "$3" "$3")
POD
}

check() {
    local ns fail=0 quota_cpu quota_mem live_cpu live_mem n shown over out
    printf '%-13s %-26s %-26s %s\n' namespace quota-cpu/memory pool-allocatable-cpu/memory objects
    for ns in "${namespaces[@]}"; do
        read -r quota_cpu quota_mem <<<"$(k -n "$ns" get resourcequota pool-capacity -o jsonpath='{.spec.hard.requests\.cpu} {.spec.hard.requests\.memory}')"
        read -r live_cpu live_mem < <(live_alloc "$ns")
        n=$(k -n "$ns" get resourcequota,limitrange --no-headers | wc -l)
        shown=ok
        [ "$n" -eq 2 ] || { shown="$n objects, want 2"; fail=1; }
        if [ "$ns" = wdf-workers ]; then
            { [ "$(to_milli "$quota_cpu")" -gt "$live_cpu" ] && [ "$(to_mib "$quota_mem")" -gt "$live_mem" ]; } || { shown="quota not above allocatable"; fail=1; }
        fi
        printf '%-13s %-26s %-26s %s\n' "$ns" "${quota_cpu}/${quota_mem}" "${live_cpu}m/${live_mem}Mi" "$shown"
    done
    for ns in "${namespaces[@]}"; do
        k -n "$ns" delete pod probe-defaults probe-over --ignore-not-found >/dev/null
        probe_pod "$ns" probe-defaults | k apply -f - >/dev/null
        echo "$ns defaults: $(k -n "$ns" get pod probe-defaults -o jsonpath='requests {.spec.containers[0].resources.requests} limits {.spec.containers[0].resources.limits}')"
        k -n "$ns" delete pod probe-defaults --wait=false >/dev/null
        read -r _ quota_mem < <(quota_for "$ns")
        over=$((quota_mem + 1))
        if out=$(probe_pod "$ns" probe-over "$over" | k apply -f - 2>&1); then
            echo "$ns over-quota: ADMITTED, expected rejection"; k -n "$ns" delete pod probe-over --wait=false >/dev/null; fail=1
        else
            echo "$ns over-quota: rejected: $out"
        fi
    done
    return $fail
}

case $mode in
    render) render_quotas ;;
    apply)
        k apply -f deploy/namespaces/namespaces.yaml
        render_quotas | k apply -f -
        ;;
    check) check ;;
esac
