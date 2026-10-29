#!/usr/bin/env bash
# Applies the hard limits of a rendered limits.yaml to the node containers, because kind and ctlptl have no limit setting.
# Memory swap equals memory, so a node has no swap and cannot grow past its size, and a node restarts with the daemon.
# Usage: limits.sh <limits.yaml>. Exit 0 applied, 1 a node container is missing, 2 usage.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
# shellcheck source=/dev/null
source scripts/verify/cluster/lib/tools.sh

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: limits.sh <limits.yaml>" >&2; exit 2; }

yq '.nodes[] | .container + " " + .cpus + " " + .memory_mib + " " + .memory_swap_mib' "$1" |
    while read -r container cpus mem swap; do
        docker update --cpus "$cpus" --memory "${mem}m" --memory-swap "${swap}m" --restart unless-stopped "$container" >/dev/null ||
            { echo "limits: cannot update $container" >&2; exit 1; }
        echo "limits: $container cpus=$cpus memory=${mem}Mi swap=${swap}Mi restart=unless-stopped"
    done
