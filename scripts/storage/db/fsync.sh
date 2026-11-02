#!/usr/bin/env bash
# Runs pg_test_fsync in the app database pod on its volume claim and prints its figures and the df line of that volume.
# The figures are recorded and judged by the reader, there is no pass line.
# Usage: fsync.sh. Exit 0 the probe ran, 1 it did not.
set -euo pipefail

context=kind-wdf
data=/var/lib/postgresql/data
k() { kubectl --context "$context" -n wdf-infra "$@"; }

probe=$data/fsync-probe
trap 'k exec statefulset/appdb -- rm -f "$probe"' EXIT

k exec statefulset/appdb -- pg_test_fsync -f "$probe"
k exec statefulset/appdb -- df -h "$data"
