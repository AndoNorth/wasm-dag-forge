#!/usr/bin/env bash
# Regenerate docs/architecture/*.drawio.svg from the .drawio sources.
# --check: fail if an SVG is missing or older than its source (no Docker needed).
set -euo pipefail

dir="$(cd "$(dirname "$0")/../docs/architecture" && pwd)"
shopt -s nullglob

if [ "${1:-}" = "--check" ]; then
  rc=0
  for src in "$dir"/*.drawio; do
    svg="$src.svg"
    if [ ! -f "$svg" ] || [ "$src" -nt "$svg" ]; then
      echo "stale or missing: ${svg#"$dir"/}" >&2
      rc=1
    fi
  done
  exit $rc
fi

for src in "$dir"/*.drawio; do
  name="$(basename "$src")"
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$dir:/data" -w /data rlespinasse/drawio-desktop-headless \
    -x -f svg -e -b 10 --theme light -o "/data/$name.svg" "/data/$name"
done
