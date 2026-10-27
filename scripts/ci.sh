#!/usr/bin/env bash
# The CI gate: the same command runs locally and in .github/workflows/ci.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts/lint.sh
scripts/test.sh
scripts/itest.sh
nix flake check
if [ -x scripts/verify-lint.sh ]; then
    scripts/verify-lint.sh
else
    echo "notice: scripts/verify-lint.sh not present yet, skipping verify-lint"
fi
