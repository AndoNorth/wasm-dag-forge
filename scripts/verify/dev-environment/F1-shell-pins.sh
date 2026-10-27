#!/usr/bin/env bash
# F1: the dev shell resolves every pinned tool from the flake.
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/lib.sh"

# command | derivation name prefix | version arguments
tools='kind|kind|version
ctlptl|ctlptl|version
kubectl|kubectl|version --client
helm|kubernetes-helm|version --short
tilt|tilt|version
just|just|--version
uv|uv|--version
python3|python3|--version
ruff|ruff|--version
yamllint|yamllint|--version
shellcheck|shellcheck|--version
actionlint|actionlint|--version
kubeconform|kubeconform|-v
nixfmt|nixfmt|--version'

cap pinned-names nix eval --raw --apply 'ps: builtins.concatStringsSep "\n" (map (p: p.name) ps)' \
  "path:$TREE#devShells.x86_64-linux.default.nativeBuildInputs"
cp "$LAST_OUT" "$EVID_F/drvnames.txt"
names=$(cat "$LAST_OUT")

: >"$EVID_F/which-a.txt"
while IFS='|' read -r cmd prefix vargs; do
  cap "path-$cmd" nd bash -c "command -v $cmd; type -aP $cmd"
  { echo "## $cmd"; cat "$LAST_OUT"; } >>"$EVID_F/which-a.txt"
  first=$(sed -n 1p "$LAST_OUT")
  case $first in
    "$HOME"/.nix-profile/* | /nix/var/nix/profiles/*) bad "path-$cmd" "shadowed by profile: $first" ;;
    /nix/store/*) ok "path-$cmd" "$first" ;;
    *) bad "path-$cmd" "not from the flake: ${first:-not found}" ;;
  esac

  want=$(printf '%s\n' "$names" | grep -m1 "^$prefix-[0-9]" | sed "s/^$prefix-//" || true)
  # shellcheck disable=SC2086
  cap "version-$cmd" nd $cmd $vargs
  if [ -n "$want" ] && grep -Fq "$want" "$LAST_OUT" "$LAST_ERR"; then
    ok "version-$cmd" "$want matches the lock"
  else
    bad "version-$cmd" "expected ${want:-<no derivation>}, got: $(head -1 "$LAST_OUT")"
  fi
done <<<"$tools"

cap helm-major nd helm version --short
case $(cat "$LAST_OUT") in v3*) ok helm-major "$(cat "$LAST_OUT")" ;; *) bad helm-major "not v3: $(cat "$LAST_OUT")" ;; esac

cap flake-check nix flake check
is flake-check "exit 0" "exit $RC" [ "$RC" = 0 ]

if [ -f "$TREE/nix/wasm-tools.nix" ]; then
  bad wasm-tools "nix/wasm-tools.nix exists: extend F1 with the wasmtime and wasm-tools versions and the corrupt-hash check"
else
  skipc wasm-tools "wasmtime and wasm-tools not pinned yet (lane E, nix/wasm-tools.nix absent)"
  skipc corrupt-hash "needs nix/wasm-tools.nix (lane E)"
fi

if [ -f "$TREE/deploy/versions.yaml" ]; then
  cap versions nd just versions
  is versions "just versions exit 0" "just versions exit $RC" [ "$RC" = 0 ]
else
  skipc versions "deploy/versions.yaml absent (lane E)"
fi

finish
