#!/usr/bin/env bash
# Usage: install.sh <toplevel> <hook-config>
# Called by the dev shell on every entry. Links the rendered hook config into the checkout and installs the
# pre-commit and pre-push shims into the common git dir, so linked worktrees share them. A core.hooksPath that
# a person set in a config file is left alone; one injected for a single command through the environment is not
# a choice about this repository and does not stop the install.
set -euo pipefail

top=$1 config=$2
link="$top/.pre-commit-config.yaml"

persisted=$(git -C "$top" config --show-scope --get-all core.hooksPath | awk -F'\t' '$1 != "command" { print $2 }' || true)
if [ -n "$persisted" ]; then
    echo "git hooks not installed: core.hooksPath is set to $persisted" >&2
    exit 0
fi

# A regular file there is someone's own config and stays.
if [ ! -e "$link" ] || [ -L "$link" ]; then
    [ "$(readlink "$link" 2>/dev/null)" = "$config" ] || ln -sfn "$config" "$link"
fi

# A missing config makes a shim do nothing, so a worktree that never entered the shell is never blocked.
prek -q -C "$top" install --allow-missing-config -c .pre-commit-config.yaml -t pre-commit -t pre-push \
    --git-dir "$(git -C "$top" rev-parse --path-format=absolute --git-common-dir)"
