#!/usr/bin/env bash
# Provisions the object store in the cluster through a port-forward bound to 127.0.0.1: the four buckets, the lifecycle
# rules of contracts/store/lifecycle.yaml and the detection interval of the lifecycle job, all with the admin identity.
# Waits for the pod first, so it can run as a Tilt local_resource or by hand.
#   apply  create what is missing and set the rules (default)
#   check  exit 1 when the store differs from the contract
# Usage: bootstrap.sh [apply|check]. Exit 0 ok, 1 the store or the forward did not come up or differs, 2 usage.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

mode=${1:-apply}
case $mode in apply | check) ;; *) echo "usage: bootstrap.sh [apply|check]" >&2; exit 2 ;; esac

context=kind-wdf
namespace=wdf-infra
s3_port=8333
admin_port=12535
k() { kubectl --context "$context" -n "$namespace" "$@"; }

k wait --for=condition=Ready pod/objectstore-0 --timeout=300s >/dev/null

log=$(mktemp)
k port-forward --address 127.0.0.1 pod/objectstore-0 ":$s3_port" ":$admin_port" >"$log" 2>&1 &
forward=$!
trap 'kill "$forward" 2>/dev/null || true; rm -f "$log"' EXIT

# The forward picks free local ports and prints each once it listens.
local_port() { sed -n "s/^Forwarding from 127\.0\.0\.1:\([0-9]*\) -> $1\$/\1/p" "$log" | head -n 1; }
s3_local=
admin_local=
for _ in $(seq 60); do
    s3_local=$(local_port "$s3_port")
    admin_local=$(local_port "$admin_port")
    [ -n "$s3_local" ] && [ -n "$admin_local" ] && break
    kill -0 "$forward" 2>/dev/null || { cat "$log" >&2; exit 1; }
    sleep 0.5
done
[ -n "$s3_local" ] && [ -n "$admin_local" ] || { echo "port-forward to objectstore-0 did not come up" >&2; exit 1; }

uv run --frozen --quiet python scripts/storage/store/bootstrap.py "$mode" \
    "http://127.0.0.1:$s3_local" "http://127.0.0.1:$admin_local" "$(scripts/storage/store/identities.sh path)"
