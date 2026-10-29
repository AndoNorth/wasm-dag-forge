#!/usr/bin/env bash
# Brings up the kind cluster wdf and its local registry for WDF_PRESET (default ci) through ctlptl.
# A second run with nothing changed leaves every container as it was.
# Exit 0 every node Ready, 1 the cluster did not come up, 2 usage, 4 refused: the preset needs more memory than the
# host has, or the cluster wdf is one this script did not start (or started for another preset).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=/dev/null
source scripts/verify/cluster/lib/tools.sh

preset=${WDF_PRESET:-ci}
state=${WDF_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/wasm-dag-forge}
context=kind-wdf

refuse() { echo "refused: $*" >&2; exit 4; }

# Every recipe that touches the cluster holds one lock, unless a caller already holds it for a longer sequence.
if [ -z "${WDF_CLUSTER_LOCK_HELD:-}" ]; then
    exec 9>/tmp/wdf-cluster.lock
    flock -w 900 9 || { echo "could not take /tmp/wdf-cluster.lock" >&2; exit 1; }
fi

out=$(scripts/cluster/render.sh "$preset") || exit $?

need=$(yq '.memory_mib' "deploy/presets/$preset.yaml")
have=$(($(sed -n 's/^MemTotal: *\([0-9]*\) kB.*/\1/p' /proc/meminfo) / 1024))
[ "$have" -ge "$need" ] || refuse "preset $preset needs $need MiB and the host has $have MiB"

# The owner file is written before the cluster exists, so a run that dies half way still owns what it created.
if kind get clusters 2>/dev/null | grep -qx wdf; then
    started=$(cat "$state/cluster.owner" 2>/dev/null || true)
    [ -n "$started" ] || refuse "a kind cluster named wdf exists that this script did not start, delete it with: kind delete cluster --name wdf"
    [ "$started" = "$preset" ] || refuse "cluster wdf was started for preset $started, not $preset, delete it with: kind delete cluster --name wdf"
else
    mkdir -p "$state"
    echo "$preset" >"$state/cluster.owner"
fi

# A node container that exited is started again, because the API server keeps reporting its node Ready for a while.
stopped=$(docker ps -aq --filter label=io.x-k8s.kind.cluster=wdf --filter status=exited --filter status=created)
# shellcheck disable=SC2086
# ctlptl recreates a cluster whose API server does not answer, so a restarted cluster is waited for before it is applied.
if [ -n "$stopped" ]; then
    echo "starting stopped node containers: $stopped"
    docker start $stopped >/dev/null
    for _ in $(seq 120); do
        kubectl --context "$context" get --raw /readyz >/dev/null 2>&1 && break
        sleep 1
    done
fi

ctlptl apply -f "$out/registry.yaml" -f "$out/cluster.yaml"
scripts/cluster/limits.sh "$out/limits.yaml"

want=$(yq '.nodes[].count' "deploy/presets/$preset.yaml" | awk '{ n += $1 } END { print n }')
kubectl --context "$context" wait --for=condition=Ready node --all --timeout=300s
got=$(kubectl --context "$context" get nodes --no-headers | wc -l)
[ "$got" -eq "$want" ] || { echo "cluster wdf has $got nodes, preset $preset has $want" >&2; exit 1; }
# kind drops a kubeadm patch that does not match its generated apiVersion without a word, so the pool labels are checked.
pool_label=$(yq '.pool_label' contracts/topology/topology.yaml)
pooled=$(yq '.nodes[] | select(.role != "control-plane") | .count' "deploy/presets/$preset.yaml" | awk '{ n += $1 } END { print n }')
labelled=$(kubectl --context "$context" get nodes -l "$pool_label" --no-headers | wc -l)
[ "$labelled" -eq "$pooled" ] || {
    echo "$labelled of $pooled pool nodes carry the $pool_label label, delete the cluster with: kind delete cluster --name wdf" >&2
    exit 1
}
running=$(docker ps -q --filter label=io.x-k8s.kind.cluster=wdf --filter status=running | wc -l)
[ "$running" -eq "$want" ] || { echo "cluster wdf has $running running node containers, preset $preset has $want" >&2; exit 1; }
scripts/cluster/allocatable.sh "$out/limits.yaml" || exit 1
echo "cluster wdf ready: preset $preset, $got nodes"
