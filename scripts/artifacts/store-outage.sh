#!/usr/bin/env bash
# Takes the object store of the cluster away and brings it back by scaling its statefulset, which keeps the data volume.
# The caller holds the cluster lock when the run shares the cluster.
#   stop   scale to 0 and wait until the pod is gone
#   start  scale to 1 and wait until the pod is ready
# Usage: store-outage.sh stop|start. Exit 0 the store is in the asked state, 1 it did not get there, 2 usage.
set -euo pipefail

context=kind-wdf
namespace=wdf-infra
store=statefulset/objectstore
pod=pod/objectstore-0
timeout=300s
k() { kubectl --context "$context" -n "$namespace" "$@"; }

case ${1:-} in
    stop)
        k scale "$store" --replicas=0 >/dev/null
        k wait --for=delete "$pod" --timeout="$timeout" >/dev/null
        echo "object store stopped: $store scaled to 0"
        ;;
    start)
        k scale "$store" --replicas=1 >/dev/null
        # The pod does not exist for a moment after the scale, and wait fails on a missing pod.
        for _ in $(seq 60); do
            k get "$pod" >/dev/null 2>&1 && break
            sleep 1
        done
        k wait --for=condition=Ready "$pod" --timeout="$timeout" >/dev/null
        echo "object store started: $store scaled to 1 and ready"
        ;;
    *)
        echo "usage: store-outage.sh stop|start" >&2
        exit 2
        ;;
esac
