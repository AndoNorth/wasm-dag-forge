#!/usr/bin/env bash
# Usage: ci.sh [--docs | --full]
# The CI gate: the same command runs locally and in .github/workflows/ci.yml.
# The docs path skips the unit and integration tests, which no documentation file can affect.
# Without a flag, the docs path is taken when every file changed against the merge base with origin/main is documentation.
set -euo pipefail
cd "$(dirname "$0")/.."

# Committed, uncommitted and untracked changes since the merge base, or nothing when there is no origin/main.
changed() {
    local base
    base=$(git merge-base origin/main HEAD 2>/dev/null) || return 0
    git diff --name-only --no-renames "$base"
    git ls-files --others --exclude-standard
}

case "${1:-}" in
    --docs) mode=docs ;;
    --full) mode=full ;;
    "") mode=$(changed | scripts/ci-paths.sh) ;;
    *) echo "usage: ci.sh [--docs | --full]" >&2; exit 2 ;;
esac
echo "== ci path: $mode"

scripts/lint.sh
scripts/check-links.sh
echo "== contracts-map"
scripts/check-contracts-map.sh
scripts/check-contracts-map.test.sh
echo "== ci-paths"
scripts/ci-paths.test.sh
echo "== verify-lint"
scripts/lint.sh --verify-skills
if [ "$mode" = full ]; then
    scripts/test.sh
    scripts/itest.sh
fi
# checks.pre-commit reads every tracked file, documentation included, so the flake check stays on both paths.
nix flake check
