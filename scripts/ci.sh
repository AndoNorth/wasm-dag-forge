#!/usr/bin/env bash
# The CI gate: the same command runs locally and in .github/workflows/ci.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts/lint.sh
scripts/check-links.sh
echo "== contracts-map"
scripts/check-contracts-map.sh
scripts/check-contracts-map.test.sh
echo "== verify-lint"
scripts/lint.sh --verify-skills
scripts/test.sh
scripts/itest.sh
nix flake check
