#!/usr/bin/env bash
# The pre-push gate: the quick part of scripts/ci.sh. The unit and integration tests and the flake check stay with
# CI, because they take minutes and a push gate that slow gets bypassed.
set -euo pipefail
cd "$(dirname "$0")/../.."

scripts/lint.sh
echo "== verify-lint"
scripts/lint.sh --verify-skills
echo "== lint self-test"
scripts/lint.sh --self-test
