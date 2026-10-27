#!/usr/bin/env bash
# Fault-injection self-test for doctor.sh: each fault must fail exactly one check, the healthy tree none.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
doctor="$here/doctor.sh"
bash_bin=$(command -v bash)
tools=(kind ctlptl kubectl helm tilt just uv python3 ruff yamllint shellcheck actionlint kubeconform nixfmt)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fake() { printf '#!/bin/sh\n%s\n' "$2" > "$1"; chmod +x "$1"; }

# Fresh fixture: healthy tree plus fake tools in a fake store and a hermetic PATH.
build() {
  local r="$work/$1"
  mkdir -p "$r/nix/store/pkg/bin" "$r/bin" "$r/sys-bin"
  cp -r "$here/fixtures/doctor/healthy/." "$r/"
  for t in "${tools[@]}"; do fake "$r/nix/store/pkg/bin/$t" 'exit 0'; done
  for c in cat sed grep stat head dirname tr cut; do ln -s "$(command -v "$c")" "$r/sys-bin/$c"; done
  fake "$r/bin/nix" 'case "$*" in --version) echo "nix (Nix) 2.35.2";; "config show experimental-features") echo "flakes nix-command";; esac'
  fake "$r/bin/docker" 'echo 29.1.3'
}

# run <case> prints the failing ids and leaves doctor's exit code in $rc.
run() {
  local r="$work/$1"
  rc=0
  out=$(env -i HOME="$work/home" IN_NIX_SHELL=impure WDF_DOCTOR_ROOT="$r" WDF_DOCTOR_REPO="$r/${REPOSUB:-repo}" \
    PATH="${PATHPREFIX:-}$r/bin:$r/nix/store/pkg/bin:$r/sys-bin" "$bash_bin" "$doctor") || rc=$?
  failing=$(grep '^fail ' <<<"$out" | cut -d' ' -f2 | tr -d ':' | tr '\n' ' ' || true)
}

pass=0
check() {
  local name=$1 want=$2
  if [ "$want" = "" ] && [ "$rc" = 0 ] && [ -z "$failing" ]; then :
  elif [ "$want" != "" ] && [ "$rc" = 1 ] && [ "$failing" = "$want " ]; then :
  else echo "FAIL $name: rc=$rc failing='$failing' want='$want'"; echo "$out"; exit 1; fi
  echo "ok   $name"
  pass=$((pass + 1))
}

build healthy;       run healthy;       check healthy ""
build no-docker;     rm "$work/no-docker/bin/docker"; run no-docker; check no-docker docker-engine
build cgroup-v1;     rm "$work/cgroup-v1/sys/fs/cgroup/cgroup.controllers"; run cgroup-v1; check cgroup-v1 cgroup-v2
build mnt-c;         mkdir -p "$work/mnt-c/mnt/c/repo/deploy"; REPOSUB=mnt/c/repo run mnt-c; check mnt-c repo-on-ext4
build inotify;       echo 128 > "$work/inotify/proc/sys/fs/inotify/max_user_instances"; run inotify; check inotify inotify-instances
build shadow-helm;   mkdir -p "$work/shadow"; fake "$work/shadow/helm" 'exit 0'
PATHPREFIX="$work/shadow:" run shadow-helm; check shadow-helm path-provenance
grep -q "which -a: $work/shadow/helm" <<<"$out" || { echo "FAIL shadow-helm: which -a not printed"; exit 1; }
build no-flakes;     fake "$work/no-flakes/bin/nix" 'case "$*" in --version) echo "nix (Nix) 2.35.2";; "config show experimental-features") echo "nix-command";; esac'; run no-flakes; check no-flakes nix-flakes
build low-mem;       printf 'MemTotal:        2000000 kB\n' > "$work/low-mem/proc/meminfo"; run low-mem; check low-mem memory-vs-preset

# No preset file is a warning only.
build no-preset;     rm "$work/no-preset/repo/deploy/presets/ci.yaml"; run no-preset
grep -q '^warn memory-vs-preset: no preset file yet' <<<"$out" && check no-preset ""

# Outside the shell: refuse with in-dev-shell.
rc=0; out=$(env -i bash "$doctor") || rc=$?
failing=$(grep '^fail ' <<<"$out" | cut -d' ' -f2 | tr -d ':' | tr '\n' ' '); check outside-shell in-dev-shell

# --json is a valid array with the same ids.
run healthy; json=$(env -i HOME="$work/home" IN_NIX_SHELL=impure WDF_DOCTOR_ROOT="$work/healthy" WDF_DOCTOR_REPO="$work/healthy/repo" \
  PATH="$work/healthy/bin:$work/healthy/nix/store/pkg/bin:$work/healthy/sys-bin" "$bash_bin" "$doctor" --json)
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert len(d)==10 and all(set(r)=={"id","status","detail"} for r in d)' "$json"
echo "ok   json"
echo "$pass cases + json passed"
