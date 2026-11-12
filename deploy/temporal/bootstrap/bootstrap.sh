#!/usr/bin/env bash
# Bootstraps Temporal in the cluster through a port-forward to the frontend bound to 127.0.0.1: namespace wdf with its
# retention, the search attributes of the naming contract and the reaper's Schedule. Waits for the frontend first, so
# it can run as a Tilt local_resource or by hand.
#   apply  create or converge what differs and print what changed (default)
#   check  exit 1 when the server differs
# Usage: bootstrap.sh [apply|check]. Exit 0 ok, 1 the server or the forward did not come up or differs, 2 usage.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

mode=${1:-apply}
case $mode in apply | check) ;; *) echo "usage: bootstrap.sh [apply|check]" >&2; exit 2 ;; esac

context=kind-wdf
namespace=wdf-infra
port=7233
k() { kubectl --context "$context" -n "$namespace" "$@"; }

k wait --for=condition=Available deployment/temporal-frontend --timeout=300s >/dev/null

log=$(mktemp)
forward=
trap '[ -z "$forward" ] || kill "$forward" 2>/dev/null || true; rm -f "$log"' EXIT
# kubectl itself, not the k function, so $! is the forward that the trap stops.
kubectl --context "$context" -n "$namespace" port-forward --address 127.0.0.1 svc/temporal-frontend ":$port" >"$log" 2>&1 &
forward=$!

# The forward picks a free local port and prints it once it listens.
local=
for _ in $(seq 60); do
    local=$(sed -n "s/^Forwarding from 127\.0\.0\.1:\([0-9]*\) -> $port\$/\1/p" "$log" | head -n 1)
    [ -n "$local" ] && break
    kill -0 "$forward" 2>/dev/null || { cat "$log" >&2; exit 1; }
    sleep 0.5
done
[ -n "$local" ] || { echo "port-forward to temporal-frontend did not come up" >&2; exit 1; }

uv run --frozen --quiet python deploy/temporal/bootstrap/bootstrap.py "$mode" "127.0.0.1:$local"
