#!/usr/bin/env bash
# Renders the ctlptl cluster and registry files and one kubelet patch per node from a preset, without starting anything.
# Usage: render.sh <preset>. Writes <dir>/<preset>/{cluster.yaml,registry.yaml,limits.yaml,kubelet-<role>-<n>.yaml} and
# prints the directory. The kind cluster is always named wdf, so every preset renders the same context and container names.
# The directory is WDF_RENDER_DIR, or a cache directory outside the tree, because the output holds ctlptl, kubeadm and
# kubelet documents that the repo's manifest lint has no schema for.
# Exit 0 rendered, 1 the preset or a table is invalid, 2 usage.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
# shellcheck source=/dev/null
source scripts/verify/cluster/lib/tools.sh

[ $# -eq 1 ] || { echo "usage: render.sh <preset>" >&2; exit 2; }
name=$1
preset=deploy/presets/$name.yaml
[ -f "$preset" ] || { echo "usage: render.sh <preset>: no $preset" >&2; exit 2; }

contracts/preset/check.sh "$preset" >&2 || exit 1
contracts/topology/check.sh contracts/topology/topology.yaml >&2 || exit 1

ports=$(yq '.host.ports' "$preset")
if [ -f "$ports" ]; then
    contracts/preset/check.sh "$ports" >&2 || exit 1
fi

pool_label=$(yq '.pool_label' contracts/topology/topology.yaml)
taint_effect=$(yq '.taint_effect' contracts/topology/topology.yaml)
image="kindest/node:$(yq '.kind_node_image.version' deploy/versions.yaml)@$(yq '.kind_node_image.digest' deploy/versions.yaml)"
registry_image="registry:$(yq '.images.registry.version' deploy/versions.yaml)@$(yq '.images.registry.digest' deploy/versions.yaml)"
registry_name=wdf-registry
registry_port=5001
# The control plane keeps kubeadm's own taint, so it is registered without a pool label or taint of its own.
# A cpu reservation for system daemons is not part of the preset contract, so every node reserves the same amount.
SYSTEM_RESERVED_CPU=100m
# kind patches a kubeadm document only when the patch carries the apiVersion kind generated for the node image, and it
# ignores a mismatch silently. v1beta3 is what kind generates for the pinned node image, and up.sh checks the labels.
KUBEADM_API=kubeadm.k8s.io/v1beta3

out=${WDF_RENDER_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/wasm-dag-forge/cluster}/$name
rm -rf "$out"
mkdir -p "$out"

# Hard limits of every node container, one entry per kind node in kind's own naming order (control-plane, worker,
# worker2, ...). Memory swap equals memory, so no node can grow past its declared size. kind has no limit setting,
# so the cluster step applies this file with docker update before the nodes join.
limits_head() { printf 'cluster: wdf\nnodes:\n'; }
limit_entry() { # <kind node name> <role> <cpus> <memory_mib>
    printf '  - container: wdf-%s\n    role: %s\n    cpus: %s\n    memory_mib: %s\n    memory_swap_mib: %s\n' \
        "$1" "$2" "$3" "$4" "$4"
}
limits_head >"$out/limits.yaml"
kind_index=0

indent() { sed 's/^/          /'; }

# kubelet_patch <role> <memory_mib>: the kubelet configuration and the kubeadm registration of one node.
# The eviction threshold follows the rule in contracts/preset/README.md.
kubelet_patch() {
    local role=$1 mem=$2 reserved=$((256 + ($2 + 15) / 16))
    cat <<PATCH
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
systemReserved:
  cpu: $SYSTEM_RESERVED_CPU
evictionHard:
  memory.available: ${reserved}Mi
---
PATCH
    if [ "$role" = control-plane ]; then
        printf 'apiVersion: %s\nkind: InitConfiguration\n' "$KUBEADM_API"
        return
    fi
    local pool
    pool=$(yq ".pools.$role" contracts/topology/topology.yaml)
    cat <<PATCH
apiVersion: $KUBEADM_API
kind: JoinConfiguration
nodeRegistration:
  kubeletExtraArgs:
    node-labels: $pool_label=$pool
  taints:
    - key: $pool_label
      value: $pool
      effect: $taint_effect
PATCH
}

{
    cat <<HEAD
apiVersion: ctlptl.dev/v1alpha1
kind: Cluster
product: kind
name: kind-wdf
registry: $registry_name
kindV1Alpha4Cluster:
  name: wdf
  nodes:
HEAD
    # kind lists the control plane first, then the workers in preset order.
    while read -r role count mem cpus; do
        for ((n = 1; n <= count; n++)); do
            if [ "$role" = control-plane ]; then
                kind_name=control-plane
            else
                kind_name=worker$([ "$kind_index" -eq 0 ] || echo $((kind_index + 1)))
                kind_index=$((kind_index + 1))
            fi
            limit_entry "$kind_name" "$role" "$cpus" "$mem" >>"$out/limits.yaml"
            kubelet_patch "$role" "$mem" >"$out/kubelet-$role-$n.yaml"
            cat <<NODE
    - role: $([ "$role" = control-plane ] && echo control-plane || echo worker)
      image: $image
NODE
            if [ "$role" = control-plane ] && [ -f "$ports" ]; then
                yq '.ports | length' "$ports" | grep -qx 0 || {
                    echo "      extraPortMappings:"
                    yq '.ports[] | "        - containerPort: " + (.node_port | tostring) + "\n          hostPort: " + (.host_port | tostring) + "\n          listenAddress: 127.0.0.1\n          protocol: TCP"' "$ports"
                }
            fi
            echo "      kubeadmConfigPatches:"
            echo "        - |"
            indent <"$out/kubelet-$role-$n.yaml"
        done
    done < <(yq '.nodes | sort_by(.role == "control-plane" | not) | .[] | .role + " " + .count + " " + .memory_mib + " " + .cpus' "$preset")
} >"$out/cluster.yaml"

# The registry listens on loopback only. ctlptl wires it into the kind nodes as localhost:<port>.
cat >"$out/registry.yaml" <<REGISTRY
apiVersion: ctlptl.dev/v1alpha1
kind: Registry
name: $registry_name
port: $registry_port
listenAddress: 127.0.0.1
image: $registry_image
REGISTRY

echo "$out"
