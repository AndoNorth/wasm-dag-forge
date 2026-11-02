#!/usr/bin/env bash
# Runs wdf_migrate against the app database in the cluster through a port-forward bound to 127.0.0.1.
# Waits for the database to be ready first, so it can run as a Tilt local_resource or by hand.
# Usage: migrate.sh [apply|status|check] [wdf_migrate args...]. Exit codes: wdf_migrate's, 1 the database or the
# forward did not come up, 2 usage.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

command=${1:-apply}
[ $# -gt 0 ] && shift
case $command in apply | status | check) ;; *) echo "usage: migrate.sh [apply|status|check] [args...]" >&2; exit 2 ;; esac

context=kind-wdf
namespace=wdf-infra
k() { kubectl --context "$context" -n "$namespace" "$@"; }

k wait --for=condition=Ready pod/appdb-0 --timeout=300s >/dev/null

log=$(mktemp)
k port-forward --address 127.0.0.1 pod/appdb-0 :5432 >"$log" 2>&1 &
forward=$!
trap 'kill "$forward" 2>/dev/null || true; rm -f "$log"' EXIT

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
    uv run --frozen python -m wdf_migrate "$command" --dsn "postgresql://postgres@127.0.0.1:$port/postgres" "$@"
