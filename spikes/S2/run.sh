#!/usr/bin/env bash
# Footprint spike: brings up the ci preset, replaces its components with memory-holding stand-ins from a figures table,
# holds them while host MemAvailable is sampled, and checks the pass line. Run under the cluster lock and the dev shell:
#   flock /tmp/wdf-cluster.lock nix develop -c spikes/S2/run.sh <figures.tsv> <out-dir> <hold-seconds>
# The cluster is always deleted on exit. Exit 0 pass, 1 fail, 3 aborted on host memory.
set -uo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root" || exit 2
[ $# -eq 3 ] && [ -f "$1" ] || { echo "usage: run.sh <figures.tsv> <out-dir> <hold-seconds>" >&2; exit 2; }
figures=$1 out=$2 hold=$3
mkdir -p "$out"
export WDF_CLUSTER_LOCK_HELD=1
context=kind-wdf
floor_mib=1024
k() { kubectl --context "$context" "$@"; }
avail() { awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo; }
say() { echo "[$(date +%T)] $*" | tee -a "$out/run.log"; }
nodes=$(docker ps -a --filter label=io.x-k8s.kind.cluster=wdf --format '{{.Names}}' 2>/dev/null | sort)
fail=0
verdict() { if [ "$2" -eq 0 ]; then say "PASS $1: $3"; else say "FAIL $1: $3"; fail=1; fi; }

# shellcheck disable=SC2329
cleanup() {
    kill "$watch" 2>/dev/null
    say "teardown: just nuke"
    just nuke >>"$out/nuke.log" 2>&1
    say "teardown done, MemAvailable $(avail) MiB, wdf containers left: $(docker ps -aq --filter label=io.x-k8s.kind.cluster=wdf | wc -l)"
}
trap cleanup EXIT

# The watchdog is stricter than the 15 s of the method: every 5 s, and it removes the stand-ins the moment the floor is crossed.
: >"$out/memavailable.csv"
(
    while sleep 5; do
        a=$(avail)
        echo "$(date +%s),$a" >>"$out/memavailable.csv"
        if [ "$a" -lt "$floor_mib" ]; then
            touch "$out/ABORT"
            for ns in wdf-infra wdf-workers; do
                kubectl --context "$context" -n "$ns" delete pod -l wdf.dev/spike=s2 --force --grace-period 0 >/dev/null 2>&1
            done
            exit 0
        fi
    done
) &
watch=$!
aborted() { [ -e "$out/ABORT" ] && { say "ABORT: MemAvailable fell below $floor_mib MiB"; return 0; }; return 1; }

say "host before: MemAvailable $(avail) MiB, MemTotal $(awk '/^MemTotal:/ { print int($2 / 1024) }' /proc/meminfo) MiB, figures $figures"
t0=$(date +%s)
just up >"$out/up1.log" 2>&1
up1=$?
say "just up (first): exit $up1 in $(($(date +%s) - t0)) s"
tail -n 12 "$out/up1.log" | tee -a "$out/run.log"
[ "$up1" -eq 0 ] || { say "cluster did not come up"; verdict cluster-ready 1 "just up exit $up1"; exit 1; }
nodes=$(docker ps --filter label=io.x-k8s.kind.cluster=wdf --format '{{.Names}}' | sort)
scripts/cluster/namespaces.sh apply ci >"$out/namespaces.log" 2>&1 || { say "namespaces failed"; exit 1; }

# CONTROL_PLANE_MIB raises the control plane container limit alone, to test whether its memory.events max stops rising.
if [ -n "${CONTROL_PLANE_MIB:-}" ]; then
    docker update --memory "${CONTROL_PLANE_MIB}m" --memory-swap "${CONTROL_PLANE_MIB}m" wdf-control-plane >/dev/null
    say "control plane container limit raised to $CONTROL_PLANE_MIB MiB"
fi
for n in $nodes; do
    echo "== $n"
    k describe node "$n" | sed -n '/^Allocated resources/,/^Events/p' | sed '/^Events/d'
done >"$out/allocated-idle.txt"

sleep 60
say "idle cluster, no stand-ins: MemAvailable $(avail) MiB"
{
    echo "node,memory_current_MiB,limit_MiB"
    for n in $nodes; do
        cur=$(docker exec "$n" cat /sys/fs/cgroup/memory.current)
        lim=$(docker inspect -f '{{.HostConfig.Memory}}' "$n")
        echo "$n,$((cur / 1048576)),$((lim / 1048576))"
    done
} | tee "$out/idle-nodes.csv" | tee -a "$out/run.log"
# shellcheck disable=SC2086
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $nodes wdf-registry | tee "$out/idle-stats.txt" | tee -a "$out/run.log"

counter() { docker exec "$1" awk -v key="$2" '$1 == key { print $2 }' /sys/fs/cgroup/memory.events; }
for n in $nodes; do echo "$n $(counter "$n" oom_kill) $(counter "$n" max)"; done >"$out/events-before.txt"
ids_before=$(docker ps -q --filter label=io.x-k8s.kind.cluster=wdf | sort | tr '\n' ' ')

pin="busybox:$(yq '.images.canary.version' deploy/versions.yaml)@$(yq '.images.canary.digest' deploy/versions.yaml)"
docker pull -q "$pin" >/dev/null && docker tag "$pin" localhost:5001/canary:pinned && docker push -q localhost:5001/canary:pinned >/dev/null

pool_label=$(yq '.pool_label' contracts/topology/topology.yaml)
grep -v '^#' "$figures" | while IFS=$'\t' read -r name ns pool cpu mem; do
    aborted && break
    held=$(((mem - 32) * 1048576))
    # A stand-in whose memory would take the host under the floor is not started, because the host has other work to protect.
    if [ $(($(avail) - mem)) -lt "$floor_mib" ]; then
        say "stand-in $name on $pool NOT STARTED: MemAvailable $(avail) MiB less $mem MiB is under the $floor_mib MiB floor"
        echo "$name" >>"$out/not-started.txt"
        continue
    fi
    k apply -f - >/dev/null <<POD
apiVersion: v1
kind: Pod
metadata:
  name: $name
  namespace: $ns
  labels: {wdf.dev/spike: s2}
spec:
  restartPolicy: Never
  nodeSelector: {$pool_label: $pool}
  tolerations: [{key: $pool_label, operator: Exists}]
  containers:
    - name: ballast
      image: localhost:5001/canary:pinned
      command: ["sh", "-c", "(head -c $held /dev/zero; exec sleep 86400) | tail -c $held >/dev/null"]
      resources:
        requests: {cpu: "$cpu", memory: ${mem}Mi}
        limits: {cpu: "$cpu", memory: ${mem}Mi}
POD
    for _ in $(seq 15); do
        phase=$(k -n "$ns" get pod "$name" -o jsonpath='{.status.phase}')
        [ "$phase" = Running ] && break
        aborted && break
        sleep 2
    done
    say "stand-in $name on $pool: ${cpu} cpu, ${mem} MiB requested, held $((held / 1048576)) MiB, phase ${phase:-unknown}, MemAvailable $(avail) MiB"
    if [ "$phase" != Running ]; then
        k -n "$ns" get events --field-selector "involvedObject.name=$name" -o jsonpath='{range .items[*]}{.reason}: {.message}{"\n"}{end}' | tee -a "$out/run.log"
    fi
done

say "holding for $hold s"
end=$(($(date +%s) + hold))
while [ "$(date +%s)" -lt "$end" ] && ! aborted; do sleep 5; done

say "state after the hold"
k get nodes -L "$pool_label" | tee -a "$out/run.log"
k get pods -A -l wdf.dev/spike=s2 -o wide | tee -a "$out/run.log"
{
    echo "node,memory_current_MiB,limit_MiB,oom_kill_before,oom_kill_after,max_before,max_after"
    for n in $nodes; do
        cur=$(docker exec "$n" cat /sys/fs/cgroup/memory.current)
        lim=$(docker inspect -f '{{.HostConfig.Memory}}' "$n")
        read -r _ ob mb < <(grep "^$n " "$out/events-before.txt")
        echo "$n,$((cur / 1048576)),$((lim / 1048576)),$ob,$(counter "$n" oom_kill),$mb,$(counter "$n" max)"
    done
} | tee "$out/loaded-nodes.csv" | tee -a "$out/run.log"
# shellcheck disable=SC2086
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $nodes | tee "$out/loaded-stats.txt" | tee -a "$out/run.log"

min=$(cut -d, -f2 "$out/memavailable.csv" | sort -n | head -1)
want_nodes=$(yq '.nodes[].count' deploy/presets/ci.yaml | awk '{ n += $1 } END { print n }')
ready=$(k get nodes --no-headers | awk '$2 == "Ready"' | wc -l)
want_pods=$(grep -vc '^#' "$figures")
running=$(k get pods -A -l wdf.dev/spike=s2 --no-headers | awk '$4 == "Running"' | wc -l)
oom=$(awk -F, 'NR > 1 && $5 > $4 { n++ } END { print n + 0 }' "$out/loaded-nodes.csv")
maxrise=$(awk -F, 'NR > 1 && $7 > $6 { n++ } END { print n + 0 }' "$out/loaded-nodes.csv")

if aborted; then say "RESULT aborted"; exit 3; fi
t1=$(date +%s)
just up >"$out/up2.log" 2>&1
up2=$?
ids_after=$(docker ps -q --filter label=io.x-k8s.kind.cluster=wdf | sort | tr '\n' ' ')
say "just up (second, under load): exit $up2 in $(($(date +%s) - t1)) s, containers unchanged: $([ "$ids_before" = "$ids_after" ] && echo yes || echo no)"
tail -n 4 "$out/up2.log" | tee -a "$out/run.log"

verdict nodes-ready "$([ "$ready" -eq "$want_nodes" ] && echo 0 || echo 1)" "$ready of $want_nodes nodes Ready"
verdict stand-ins-running "$([ "$running" -eq "$want_pods" ] && echo 0 || echo 1)" "$running of $want_pods stand-ins Running"
verdict host-floor "$([ "$min" -ge "$floor_mib" ] && [ ! -e "$out/not-started.txt" ] && echo 0 || echo 1)" "lowest MemAvailable $min MiB, floor $floor_mib MiB, not started for the floor: $(tr '\n' ' ' <"$out/not-started.txt" 2>/dev/null)"
verdict oom-kill "$([ "$oom" -eq 0 ] && echo 0 || echo 1)" "$oom nodes with oom_kill above before"
verdict memory-max "$([ "$maxrise" -eq 0 ] && echo 0 || echo 1)" "$maxrise nodes with memory.events max rising"
verdict up-again "$([ "$up2" -eq 0 ] && echo 0 || echo 1)" "just up under load exit $up2"
say "RESULT $([ "$fail" -eq 0 ] && echo pass || echo fail)"
exit "$fail"
