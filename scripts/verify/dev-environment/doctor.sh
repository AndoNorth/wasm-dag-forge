#!/usr/bin/env bash
# Usage: doctor.sh [run dir]
# Read-only. Is this host worth driving? One line per check: ok|warn|fail <id>: <detail>.
# WDF_VERIFY_SKIP_DOCKER=1 skips the Docker check (T0 features never touch Docker).
# WDF_VERIFY_LOCK_OWNED=1 is set by run.sh once it holds the lock itself.
set -uo pipefail
run_dir=${1:-}
lock=/tmp/wdf-verify-dev-environment.lock
status=0
say() {
  echo "$1 $2: $3"
  [ "$1" != fail ] || status=1
}

if nix --version >/dev/null 2>&1 && nix show-config 2>/dev/null | grep -Eq '^experimental-features = .*flakes'; then
  say ok nix-flakes "$(nix --version)"
else
  say fail nix-flakes "nix missing or flakes not enabled"
fi

if [ -n "${WDF_VERIFY_SKIP_DOCKER:-}" ]; then
  say warn docker "skipped by WDF_VERIFY_SKIP_DOCKER"
elif docker info >/dev/null 2>&1; then
  say ok docker "engine reachable"
else
  say fail docker "engine not reachable"
fi

free=$(df --output=avail -B1 /nix/store 2>/dev/null | tail -1 | tr -d ' ')
if [ "${free:-0}" -ge $((5 * 1024 ** 3)) ]; then
  say ok store-space "$((free / 1024 ** 3)) GiB free"
else
  say fail store-space "under 5 GiB free on the store filesystem"
fi

if [ -n "${WDF_VERIFY_LOCK_OWNED:-}" ]; then
  say ok lock "held by this run"
elif (exec 8>"$lock" && flock -n 8); then
  say ok lock "free"
else
  say fail lock "$lock is held by another run"
fi

if [ -n "$run_dir" ]; then
  if [ "$(stat -c %u "$run_dir")" = "$(id -u)" ]; then
    say ok run-dir "owned by caller"
  else
    say fail run-dir "$run_dir not owned by caller"
  fi
  if [ "$(sha256sum "$run_dir/tree/flake.lock" | cut -d' ' -f1)" = "$(cat "$run_dir/flake.lock.sha256")" ]; then
    say ok flake-lock "unchanged since launch"
  else
    say fail flake-lock "hash differs from the one recorded at launch"
  fi
fi
exit "$status"
