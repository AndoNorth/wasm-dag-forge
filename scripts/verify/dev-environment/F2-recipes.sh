#!/usr/bin/env bash
# F2: the justfile is the entrypoint contract, against stub and real scripts alike.
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/lib.sh"

contract='up down nuke cluster-verify test itest lint smoke e2e load-test characterize ci doctor versions'
extra='verify verify-lint'

cap list nd just --summary
got=$(tr ' ' '\n' <"$LAST_OUT" | sort | tr '\n' ' ')
missing=
for r in $contract; do grep -qw -- "$r" <<<"$got" || missing="$missing $r"; done
unexpected=
for r in $got; do
  case " $contract $extra " in *" $r "*) ;; *) unexpected="$unexpected $r" ;; esac
done
is contract-names "all 14 present" "missing:$missing" [ -z "$missing" ]
is extra-names "only $extra beyond the contract" "unexpected:$unexpected" [ -z "$unexpected" ]

# recipes that stay stubs through epic #1, with their owner issue text
while read -r r owner; do
  cap "stub-$r" nd just "$r"
  if [ "$RC" = 3 ] && grep -Fq "not implemented: owned by #$owner" "$LAST_ERR"; then
    ok "stub-$r" "exit 3, owned by #$owner"
  else
    bad "stub-$r" "expected exit 3 and 'owned by #$owner', got exit $RC"
  fi
done <<'STUBS'
up 2
down 2
nuke 2
cluster-verify 2
smoke 17
e2e 17
load-test 14
characterize 16
STUBS

calls_script() { grep -Fq "scripts/$1.sh" "$LAST_OUT" && [ -x "$TREE/scripts/$1.sh" ]; }
# every other recipe calls scripts/<name>.sh and keeps no logic in the justfile
for r in test itest lint ci doctor versions; do
  cap "body-$r" nd just --show "$r"
  is "body-$r" "calls scripts/$r.sh" "does not call an executable scripts/$r.sh" calls_script "$r"
done

for m in "$TREE"/just/*.just; do
  is "import-$(basename "$m")" imported "not imported by the justfile" \
    grep -Fq "import 'just/$(basename "$m")'" "$TREE/justfile"
done

cap versions nd just versions
if [ "$RC" = 0 ]; then
  ok versions "just versions exit 0"
elif [ "$RC" = 3 ] && grep -Fq 'not implemented: owned by #' "$LAST_ERR"; then
  skipc versions "still a stub: $(head -1 "$LAST_ERR")"
else
  bad versions "exit $RC"
fi

finish
