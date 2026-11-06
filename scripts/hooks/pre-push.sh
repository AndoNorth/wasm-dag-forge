#!/usr/bin/env bash
# Usage: pre-push.sh [check]
# The pre-push gate: the quick part of scripts/ci.sh. The unit and integration tests and the flake check stay with
# CI, because they take minutes and a push gate that slow gets bypassed.
# With a check name (a lint step, verify-lint, self-test or contracts-map) only that check runs, which is how the pre-push hooks
# run each check only for the files that concern it. Without one, every check runs.
set -euo pipefail
cd "$(dirname "$0")/../.."

case "${1:-all}" in
    all)
        scripts/lint.sh
        "$0" verify-lint
        "$0" self-test ;;
    contracts-map) echo "== contracts-map"; scripts/check-contracts-map.sh ;;
    verify-lint) echo "== verify-lint"; scripts/lint.sh --verify-skills ;;
    self-test) echo "== lint self-test"; scripts/lint.sh --self-test ;;
    *) scripts/lint.sh --only "$1" ;;
esac
