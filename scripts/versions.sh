#!/usr/bin/env bash
# Prints the pinned tool versions and deploy/versions.yaml entries, one key per line.
# --check validates deploy/versions.yaml against contracts/versions.schema.json and exits 1 on failure.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# nixpkgs comes from the flake lock, so the validator and yaml reader are pinned with the shell.
with_nix_tools() {
  nix shell --inputs-from "$root" nixpkgs#check-jsonschema nixpkgs#yq-go -c "$@"
}

check() {
  with_nix_tools check-jsonschema --schemafile "$root/contracts/versions.schema.json" "$root/deploy/versions.yaml"
}

case "${1:-}" in
  --check)
    check
    exit
    ;;
  "") ;;
  *)
    echo "usage: versions.sh [--check]" >&2
    exit 2
    ;;
esac

missing=0
tool() {
  local name="$1"
  shift
  local out
  if ! out="$("$@" 2>&1)"; then
    echo "tool.$name: missing"
    missing=1
    return
  fi
  echo "tool.$name: $(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' <<<"$out" | head -1)"
}

tool kind kind version
tool ctlptl ctlptl version
tool kubectl kubectl version --client
tool helm helm version --short
tool tilt tilt version
tool just just --version
tool uv uv --version
tool python python3 --version
tool ruff ruff --version
tool yamllint yamllint --version
tool shellcheck shellcheck --version
tool actionlint actionlint --version
tool kubeconform kubeconform -v
tool nixfmt nixfmt --version
tool wasmtime wasmtime --version
tool wasm-tools wasm-tools --version

check >/dev/null
with_nix_tools yq -o=props '.' "$root/deploy/versions.yaml" | sed -e '/^#/d' -e '/^$/d' -e 's/ = /: /' -e 's/^/pin./'
exit "$missing"
