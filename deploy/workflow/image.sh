#!/usr/bin/env bash
# Builds the image of the workflow worker service from this tree: wheels of the workspace packages that wdf-workflow
# needs, built on the host, over the third-party packages that uv.lock pins by hash, on the pinned Python base.
# The build id is WDF_BUILD_ID, or the short commit when it is unset, and the image carries it as WDF_BUILD_ID.
# Usage: image.sh [REF]. REF defaults to EXPECTED_REF, which Tilt sets. Exit 0 built, 1 no build id or a failed build,
# 2 usage.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

ref=${1:-${EXPECTED_REF:-}}
[ -n "$ref" ] && [ $# -le 1 ] || { echo "usage: image.sh [REF]" >&2; exit 2; }
build_id=${WDF_BUILD_ID:-$(git rev-parse --short HEAD 2>/dev/null || true)}
[ -n "$build_id" ] || { echo "no build id: set WDF_BUILD_ID outside a git checkout" >&2; exit 1; }

base="python:$(yq '.images.python.version' deploy/versions.yaml)@$(yq '.images.python.digest' deploy/versions.yaml)"
context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT

uv export --frozen --quiet --no-dev --package wdf-workflow --no-emit-workspace -o "$context/requirements.txt"
uv export --frozen --quiet --no-dev --package wdf-workflow --only-emit-workspace --no-editable --no-hashes \
    --no-annotate --no-header | while read -r package; do
    uv build --quiet --wheel --out-dir "$context/wheels" "$package"
done

docker build --quiet -f deploy/workflow/Dockerfile -t "$ref" \
    --build-arg "BASE=$base" --build-arg "WDF_BUILD_ID=$build_id" \
    --label "wdf.dev/build-id=$build_id" "$context" >/dev/null
echo "built $ref as build $build_id"
