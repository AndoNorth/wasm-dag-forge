#!/usr/bin/env bash
# Usage: export.sh <run dir> [--dirty]
# Exports a clean tree to <run dir>/tree and records what was exported.
# Committed state by default (what CI sees), --dirty copies tracked and untracked unignored files.
set -euo pipefail
run_dir=${1:?usage: export.sh <run dir> [--dirty]}
mode=${2:-}
root=$(cd "$(dirname "$0")/../../.." && pwd)
mkdir -p "$run_dir/tree"
cd "$root"
if [ "$mode" = --dirty ]; then
  git ls-files -co --exclude-standard -z | tar --null --ignore-failed-read -T - -c | tar -x -C "$run_dir/tree"
else
  git archive HEAD | tar -x -C "$run_dir/tree"
fi
git rev-parse HEAD >"$run_dir/commit"
sha256sum "$run_dir/tree/flake.lock" | cut -d' ' -f1 >"$run_dir/flake.lock.sha256"
nix develop "$run_dir/tree" -c just --version
