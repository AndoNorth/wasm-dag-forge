#!/usr/bin/env bash
# Creates or checks the service logins of the workflow worker in the app database, as its superuser, through a
# port-forward bound to 127.0.0.1. Waits for the migrated database first, so it can run as a Tilt local_resource or by
# hand, and running it again changes nothing.
#   apply  create or converge the logins and print what changed (default)
#   check  exit 1 when a login differs or cannot log in
# Usage: logins.sh [apply|check]. Exit 0 ok, 1 the database or the forward did not come up or a login differs, 2 usage.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

mode=${1:-apply}
case $mode in apply | check) ;; *) echo "usage: logins.sh [apply|check]" >&2; exit 2 ;; esac

context=kind-wdf
namespace=wdf-infra
k() { kubectl --context "$context" -n "$namespace" "$@"; }

k wait --for=condition=Ready pod/appdb-0 --timeout=300s >/dev/null

log=$(mktemp)
forward=
trap '[ -z "$forward" ] || kill "$forward" 2>/dev/null || true; rm -f "$log"' EXIT
# kubectl itself, not the k function, so $! is the forward that the trap stops.
kubectl --context "$context" -n "$namespace" port-forward --address 127.0.0.1 pod/appdb-0 :5432 >"$log" 2>&1 &
forward=$!

# The forward picks a free local port and prints it once it listens.
port=
for _ in $(seq 60); do
    port=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\) .*/\1/p' "$log" | head -n 1)
    [ -n "$port" ] && break
    kill -0 "$forward" 2>/dev/null || { cat "$log" >&2; exit 1; }
    sleep 0.5
done
[ -n "$port" ] || { echo "port-forward to appdb-0 did not come up" >&2; exit 1; }

PGPASSWORD=$(scripts/storage/db/secrets.sh password) \
    uv run --frozen --quiet python deploy/workflow/logins.py "$mode" "postgresql://postgres@127.0.0.1:$port/postgres"
