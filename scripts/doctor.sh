#!/usr/bin/env bash
# Host preflight: one line per check "ok|warn|fail <id>: <detail>", exit 1 if any fail.
# WDF_DOCTOR_ROOT prefixes every host path read (proc, sys, flake store) so the self-test can point at a fixture tree.
set -uo pipefail

ROOT="${WDF_DOCTOR_ROOT:-}"
REPO="${WDF_DOCTOR_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PRESET="${WDF_PRESET:-ci}"
MIN_INOTIFY=512
MIN_DOCKER_MAJOR=24
TOOLS=(kind ctlptl kubectl helm tilt just uv python3 ruff yamllint shellcheck actionlint kubeconform nixfmt yq wasmtime wasm-tools)
# docker stays a host prerequisite, checked by docker-engine, so it is not in TOOLS.

json=0
case "${1:-}" in
  "") ;;
  --json) json=1 ;;
  *) echo "usage: doctor.sh [--json]" >&2; exit 2 ;;
esac

failed=0
rows=()

esc() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; printf '%s' "${s//$'\t'/\\t}"; }

emit() {
  rows+=("$1|$2|$3")
  [ "$1" = fail ] && failed=1
  [ "$json" = 1 ] || echo "$1 $2: $3"
  return 0
}

finish() {
  if [ "$json" = 1 ]; then
    local sep="" r s i d
    printf '['
    for r in "${rows[@]}"; do
      s=${r%%|*}; r=${r#*|}; i=${r%%|*}; d=${r#*|}
      printf '%s{"id":"%s","status":"%s","detail":"%s"}' "$sep" "$i" "$s" "$(esc "$d")"
      sep=","
    done
    printf ']\n'
  fi
  exit "$failed"
}

# Outside the flake shell host copies of tools can shadow the pins, so nothing else is meaningful.
if [ -z "${IN_NIX_SHELL:-}" ]; then
  emit fail in-dev-shell "not inside the flake dev shell, run: nix develop -c just doctor"
  finish
fi
emit ok in-dev-shell "IN_NIX_SHELL=$IN_NIX_SHELL"

if ! command -v nix >/dev/null; then
  emit fail nix-flakes "nix not found"
else
  nixv=$(nix --version 2>&1 | head -n1)
  feats=$(nix config show experimental-features 2>/dev/null)
  if [[ " $feats " == *" flakes "* && " $feats " == *" nix-command "* ]]; then
    emit ok nix-flakes "$nixv, experimental-features: $feats"
  else
    emit fail nix-flakes "$nixv, flakes and nix-command not both enabled (experimental-features: ${feats:-unset})"
  fi
fi

if ! command -v docker >/dev/null; then
  emit fail docker-engine "docker not found"
  emit warn docker-version "skipped, no docker engine"
elif ! dockerv=$(docker info --format '{{.ServerVersion}}' 2>&1); then
  emit fail docker-engine "docker daemon not reachable: $dockerv"
  emit warn docker-version "skipped, no docker engine"
else
  emit ok docker-engine "reachable"
  if [ "${dockerv%%.*}" -ge "$MIN_DOCKER_MAJOR" ] 2>/dev/null; then
    emit ok docker-version "$dockerv"
  else
    emit fail docker-version "$dockerv, need major >= $MIN_DOCKER_MAJOR"
  fi
fi

if [ -f "$ROOT/sys/fs/cgroup/cgroup.controllers" ]; then
  emit ok cgroup-v2 "unified hierarchy, kernel $(cat "$ROOT/proc/sys/kernel/osrelease" 2>/dev/null || echo unknown)"
else
  emit fail cgroup-v2 "no cgroup v2 unified hierarchy (kind needs cgroup v2)"
fi

init=$(cat "$ROOT/proc/1/comm" 2>/dev/null)
if [ "$init" = systemd ]; then
  emit ok wsl-systemd "PID 1 is systemd"
else
  emit fail wsl-systemd "PID 1 is '${init:-unknown}', enable systemd in /etc/wsl.conf"
fi

repo_path=${REPO#"$ROOT"}
fstype=$(stat -f -c %T "$REPO" 2>/dev/null || echo unknown)
case "$repo_path:$fstype" in
  /mnt/[a-z]/*:* | *:9p | *:v9fs | *:drvfs | *:fuseblk | *:cifs | *:smb2 | *:nfs)
    emit fail repo-on-ext4 "$repo_path is on $fstype, move the repo under the WSL ext4 filesystem" ;;
  *:ext2/ext3) emit ok repo-on-ext4 "$repo_path is on ext4" ;;
  *) emit warn repo-on-ext4 "$repo_path is on $fstype, not ext4" ;;
esac

inotify=$(cat "$ROOT/proc/sys/fs/inotify/max_user_instances" 2>/dev/null)
if [ "${inotify:-0}" -ge "$MIN_INOTIFY" ] 2>/dev/null; then
  emit ok inotify-instances "fs.inotify.max_user_instances=$inotify"
else
  emit fail inotify-instances "fs.inotify.max_user_instances=${inotify:-unreadable}, need >= $MIN_INOTIFY"
fi

preset="$REPO/deploy/presets/$PRESET.yaml"
if [ ! -f "$preset" ]; then
  emit warn memory-vs-preset "no preset file yet"
else
  need=$(sed -n 's/^memory_mib: *\([0-9][0-9]*\).*/\1/p' "$preset" | head -n1)
  have=$(sed -n 's/^MemTotal: *\([0-9][0-9]*\) kB.*/\1/p' "$ROOT/proc/meminfo" 2>/dev/null | head -n1)
  if [ -z "$need" ] || [ -z "$have" ]; then
    emit warn memory-vs-preset "cannot read memory_mib from $preset or MemTotal"
  elif [ $((have / 1024)) -ge "$need" ]; then
    emit ok memory-vs-preset "host $((have / 1024)) MiB >= preset $PRESET $need MiB"
  else
    emit fail memory-vs-preset "host $((have / 1024)) MiB < preset $PRESET $need MiB"
  fi
fi

bad=()
for t in "${TOOLS[@]}"; do
  mapfile -t all < <(type -aP "$t" 2>/dev/null)
  first=${all[0]:-}
  case "$first" in
    "$HOME"/.nix-profile/* | "$ROOT"/nix/var/nix/profiles/*) first="" ;;
    "$ROOT"/nix/store/*) ;;
    *) first="" ;;
  esac
  [ -n "$first" ] || bad+=("$t (which -a: ${all[*]:-none})")
done
if [ ${#bad[@]} -eq 0 ]; then
  emit ok path-provenance "${#TOOLS[@]} tools resolve first to the flake"
else
  emit fail path-provenance "resolves outside /nix/store, only the flake may provide: ${bad[*]}"
fi

finish
