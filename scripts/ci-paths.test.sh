#!/usr/bin/env bash
# Self-test for ci-paths.sh: each change set, given as space-separated paths, gets the expected path.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
classify="$here/ci-paths.sh"

pass=0
expect() {
  local want=$1 got
  shift
  got=$(printf '%s\n' "$@" | "$classify")
  [ "$got" = "$want" ] || { echo "FAIL [$*]: want $want, got $got"; exit 1; }
  echo "ok   $want [$*]"; pass=$((pass + 1))
}

expect docs docs/delivery/epics/02-cluster.md
expect docs docs/delivery/spikes/S6/results.md docs/dev-setup.md
expect docs README.md AGENTS.md
expect docs .claude/skills/verify-cluster/SKILL.md
expect docs docs/architecture/15-contracts.drawio docs/architecture/15-contracts.drawio.svg
expect docs docs/animations/src/scenes/job.tsx docs/animations/package.json
expect docs docs/delivery/spikes/S8/run.sh
expect full docs/delivery/spikes/S8/code/node.py
expect full docs/dev-setup.md packages/plan/src/wdf_plan/__init__.py
expect full contracts/state/README.md
expect full packages/temporal/README.md
expect full contracts/job/job.schema.json
expect full flake.nix
expect full flake.lock
expect full justfile
expect full just/quality.just
expect full scripts/ci.sh
expect full nix/wasm-tools.nix
expect full deploy/README.md
expect full .github/workflows/ci.yml docs/dev-setup.md
expect full .github/pull_request_template.md
expect full pyproject.toml
expect full uv.lock
expect full
echo "$pass cases passed"
