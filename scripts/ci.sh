#!/usr/bin/env bash
# The CI gate: the same command runs locally and in .github/workflows/ci.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts/lint.sh
echo "== verify-lint"
scripts/lint.sh --verify-skills
scripts/test.sh
scripts/itest.sh
nix flake check
