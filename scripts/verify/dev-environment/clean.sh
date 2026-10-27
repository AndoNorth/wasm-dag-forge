#!/usr/bin/env bash
# Usage: clean.sh <run dir>
# Removes the scratch run directory only. Never touches evidence, the Nix store or other processes.
# The lock is released when run.sh exits.
set -euo pipefail
run_dir=${1:?usage: clean.sh <run dir>}
case $run_dir in
  "${WDF_VERIFY_TMP:-/tmp}"/wdf-verify-dev-environment/*) ;;
  *)
    echo "refusing to remove $run_dir: not under the verify scratch root" >&2
    exit 2
    ;;
esac
# nix store paths copied into the tree are read-only
chmod -R u+w "$run_dir" 2>/dev/null || true
rm -rf -- "$run_dir"
