#!/usr/bin/env bash
# Self-test for check-contracts-map.sh: a matching tree passes, a missing row and a stale row each fail naming the contract.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
check="$here/check-contracts-map.sh"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Fresh tree with contracts alpha and beta, a file contract and a map row for each.
build() {
  local r="$work/$1"
  mkdir -p "$r/docs" "$r/contracts/alpha" "$r/contracts/beta"
  touch "$r/contracts/gamma.schema.json"
  cat > "$r/docs/contracts.md" <<'D'
# Contracts

| Contract | Owner |
| --- | --- |
| not in the map section | x |

## Map

| Contract | Owner |
| --- | --- |
| [alpha](../contracts/alpha/README.md) | #1 |
| [beta](../contracts/beta/README.md) | #2 |
| [gamma](../contracts/gamma.schema.json) (a schema only) | #3 |

## Later

| [delta](../contracts/delta/README.md) | outside the map section |
D
}

# run <case> leaves the exit code in $rc and the output in $out.
run() { rc=0; out=$(WDF_CONTRACTS_ROOT="$work/$1" "$check" 2>&1) || rc=$?; }

pass=0
expect() {
  local name=$1 want=$2 grepfor=${3:-}
  if [ "$want" = 0 ] && [ "$rc" = 0 ]; then :
  elif [ "$want" = 1 ] && [ "$rc" = 1 ] && grep -q "$grepfor" <<<"$out"; then :
  else echo "FAIL $name: rc=$rc out='$out'"; exit 1; fi
  echo "ok   $name"; pass=$((pass + 1))
}

build matching;  run matching;  expect matching 0
build missing;   mkdir "$work/missing/contracts/epsilon"; run missing; expect missing-row 1 'contracts/epsilon has no row'
build stale;     rm -r "$work/stale/contracts/beta"; run stale; expect stale-row 1 'row for contracts/beta,'
echo "$pass cases passed"
