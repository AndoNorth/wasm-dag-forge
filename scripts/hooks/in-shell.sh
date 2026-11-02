#!/usr/bin/env bash
# Usage: in-shell.sh <command> [args...]
# Runs the command inside the dev shell, entering it only when the caller is not already in it.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [ -n "${IN_NIX_SHELL:-}" ]; then exec "$@"; fi
exec nix develop -c "$@"
