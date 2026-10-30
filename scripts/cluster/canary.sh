#!/usr/bin/env bash
# Proves the node limits are real, with the pods in deploy/cluster/canary on the obs node, the smallest one.
#   memory    a pod that allocates past the node limit exits 137 and the node's memory.events oom_kill rises
#   schedule  a pod requesting more than allocatable stays Pending with Insufficient memory
#   pool      a pod selecting the obs pool without the toleration stays Pending on the taint, and one with it runs there
#   cpu       a spinner on the half-cpu node is throttled, so cpu.stat nr_throttled rises
# An OOM is detected by exit code 137 and the node's memory.events, never by the OOMKilled reason, which a pod at the
# limit does not always carry. memory.events max is reported next to oom_kill, because a process stalled at the limit
# raises max without a kill.
# Usage: canary.sh [memory|schedule|pool|cpu|all]. Exit 0 every requested canary passed, 1 one failed, 2 usage.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
# shellcheck source=/dev/null
source scripts/verify/cluster/lib/tools.sh

which=${1:-all}
case $which in memory | schedule | pool | cpu | all) ;; *) echo "usage: canary.sh [memory|schedule|pool|cpu|all]" >&2; exit 2 ;; esac
context=${WDF_CONTEXT:-kind-wdf}
ns=wdf-canary
k() { kubectl --context "$context" "$@"; }
# Pod manifests carry the pool, so the node is found by the same label.
node=$(k get nodes -l "$(yq '.pool_label' contracts/topology/topology.yaml)=obs" -o jsonpath='{.items[0].metadata.name}')
[ -n "$node" ] || { echo "no obs node in $context" >&2; exit 1; }

cgroup() { docker exec "$node" cat "/sys/fs/cgroup/$1"; }
counter() { cgroup "$1" | awk -v key="$2" '$1 == key { print $2 }'; }
fail=0
verdict() { # <name> <condition result 0|1> <detail>
    if [ "$2" -eq 0 ]; then echo "PASS $1: $3"; else echo "FAIL $1: $3"; fail=1; fi
}

# The canary image comes from the local registry, as every workload image does.
pin="busybox:$(yq '.images.canary.version' deploy/versions.yaml)@$(yq '.images.canary.digest' deploy/versions.yaml)"
docker pull -q "$pin" >/dev/null
docker tag "$pin" localhost:5001/canary:pinned
docker push -q localhost:5001/canary:pinned >/dev/null

trap 'k delete namespace "$ns" --ignore-not-found --wait=false >/dev/null' EXIT
k delete namespace "$ns" --ignore-not-found >/dev/null
k apply -f deploy/cluster/canary/namespace.yaml >/dev/null

memory() {
    local before_kill before_max after_kill after_max code reason ready
    before_kill=$(counter memory.events oom_kill)
    before_max=$(counter memory.events max)
    k apply -f deploy/cluster/canary/memory.yaml >/dev/null
    for _ in $(seq 120); do
        code=$(k -n "$ns" get pod canary-memory -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}')
        [ -z "$code" ] || break
        sleep 1
    done
    reason=$(k -n "$ns" get pod canary-memory -o jsonpath='{.status.containerStatuses[0].state.terminated.reason}')
    after_kill=$(counter memory.events oom_kill)
    after_max=$(counter memory.events max)
    echo "memory.events on $node: oom_kill $before_kill -> $after_kill, max $before_max -> $after_max"
    echo "container exit code: ${code:-none}, terminated reason: ${reason:-none} (reported only, not used for the verdict)"
    verdict memory-exit-code "$([ "${code:-}" = 137 ] && echo 0 || echo 1)" "pod exit code ${code:-none}, expected 137"
    verdict memory-oom-kill "$([ "$after_kill" -gt "$before_kill" ] && echo 0 || echo 1)" "node oom_kill rose by $((after_kill - before_kill))"
    k wait --for=condition=Ready "node/$node" --timeout=120s >/dev/null && ready=0 || ready=1
    verdict memory-node-ready "$ready" "$node Ready after the kill"
    k -n "$ns" delete pod canary-memory --wait=false >/dev/null
}

schedule() {
    local msg='' phase alloc_mib
    # The request is the node's own allocatable memory plus a margin, so it exceeds the node in every preset.
    alloc_mib=$(($(numfmt --from=iec-i "$(k get node "$node" -o jsonpath='{.status.allocatable.memory}')") / 1048576))
    sed "s/memory: 600Mi/memory: $((alloc_mib + 64))Mi/" deploy/cluster/canary/schedule.yaml | k apply -f - >/dev/null
    # The event does not exist until the scheduler has tried the pod, and jsonpath fails on an empty list, so a poll that finds none is retried.
    for _ in $(seq 60); do
        msg=$(k -n "$ns" get events --field-selector involvedObject.name=canary-schedule,reason=FailedScheduling \
            -o jsonpath='{.items[0].message}' 2>/dev/null || true)
        [ -z "$msg" ] || break
        sleep 1
    done
    phase=$(k -n "$ns" get pod canary-schedule -o jsonpath='{.status.phase}')
    echo "scheduler event: ${msg:-none}"
    verdict schedule-pending "$([ "$phase" = Pending ] && echo 0 || echo 1)" "pod phase $phase, expected Pending"
    verdict schedule-insufficient-memory "$([[ $msg == *"Insufficient memory"* ]] && echo 0 || echo 1)" "FailedScheduling mentions Insufficient memory"
    k -n "$ns" delete pod canary-schedule --wait=false >/dev/null
}

pool() {
    local msg='' phase placed
    k apply -f deploy/cluster/canary/pool-untolerated.yaml -f deploy/cluster/canary/pool-tolerated.yaml >/dev/null
    for _ in $(seq 60); do
        msg=$(k -n "$ns" get events --field-selector involvedObject.name=canary-pool-untolerated,reason=FailedScheduling \
            -o jsonpath='{.items[0].message}' 2>/dev/null || true)
        [ -z "$msg" ] || break
        sleep 1
    done
    phase=$(k -n "$ns" get pod canary-pool-untolerated -o jsonpath='{.status.phase}')
    echo "scheduler event: ${msg:-none}"
    verdict pool-untolerated-pending "$([ "$phase" = Pending ] && echo 0 || echo 1)" "pod without the toleration is $phase, expected Pending"
    verdict pool-untolerated-taint "$([[ $msg == *"untolerated taint"* ]] && echo 0 || echo 1)" "FailedScheduling names the untolerated taint"
    k -n "$ns" wait --for=condition=Ready pod/canary-pool-tolerated --timeout=120s >/dev/null || true
    placed=$(k -n "$ns" get pod canary-pool-tolerated -o jsonpath='{.spec.nodeName}')
    echo "tolerating pod placed on: ${placed:-none}"
    verdict pool-tolerated-placed "$([ "$placed" = "$node" ] && echo 0 || echo 1)" "pod with the toleration and selector runs on $node"
    k -n "$ns" delete pod canary-pool-untolerated canary-pool-tolerated --wait=false >/dev/null
}

cpu() {
    local before after
    before=$(counter cpu.stat nr_throttled)
    k apply -f deploy/cluster/canary/cpu.yaml >/dev/null
    k -n "$ns" wait --for=condition=Ready pod/canary-cpu --timeout=120s >/dev/null
    sleep 20
    after=$(counter cpu.stat nr_throttled)
    echo "cpu.stat on $node: nr_throttled $before -> $after"
    verdict cpu-throttled "$([ "$after" -gt "$before" ] && echo 0 || echo 1)" "node nr_throttled rose by $((after - before))"
    k -n "$ns" delete pod canary-cpu --wait=false >/dev/null
}

# The memory canary runs last: the node then reports memory pressure for several minutes.
if [ "$which" = all ]; then schedule; pool; cpu; memory; else "$which"; fi
exit $fail
