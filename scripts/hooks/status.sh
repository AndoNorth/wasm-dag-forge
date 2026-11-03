#!/usr/bin/env bash
# Prints whether the git hooks are installed for this checkout; exit 1 when one is missing.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

common=$(git rev-parse --path-format=absolute --git-common-dir)
rc=0

persisted=$(git config --show-scope --get-all core.hooksPath | awk -F'\t' '$1 != "command" { print $2 }' || true)
if [ -n "$persisted" ]; then
    echo "missing core.hooksPath: set to $persisted, the dev shell does not install hooks there"
    exit 1
fi

if [ -L .pre-commit-config.yaml ] && [ -e .pre-commit-config.yaml ]; then
    echo "ok config: .pre-commit-config.yaml -> $(readlink .pre-commit-config.yaml)"
else
    echo "missing config: .pre-commit-config.yaml, enter the shell with nix develop"
    rc=1
fi

for stage in pre-commit pre-push; do
    if grep -qs prek "$common/hooks/$stage"; then
        echo "ok $stage: $common/hooks/$stage"
    else
        echo "missing $stage: no prek shim in $common/hooks, enter the shell with nix develop"
        rc=1
    fi
done
exit $rc
