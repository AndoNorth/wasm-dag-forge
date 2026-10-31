#!/usr/bin/env bash
# Status loader: validates one status table and names the reason when it fails.
# Usage: check.sh <file>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$here/../../scripts/verify/cluster/lib/tools.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <status.yaml>" >&2; exit 2; }
file=$1

fail() {
    echo "reason: $1: $2" >&2
    exit 1
}

out=$(check-jsonschema --schemafile "$here/status.schema.json" "$file" 2>&1) ||
    fail schema-violation "$(tail -n +2 <<<"$out" | head -n3 | tr '\n' ' ')"

mapfile -t statuses < <(yq '.statuses[]' "$file")
mapfile -t views < <(yq '.views | keys | .[]' "$file")
# Every status name the table mentions outside the statuses list.
mapfile -t mentioned < <(yq '[.terminal[], .views[].shown_while[], .projection[][].sets | select(. != null), .transitions[].from, .transitions[].to] | .[]' "$file")

in_list() {
    local needle=$1
    shift
    local item
    for item in "$@"; do [ "$item" = "$needle" ] && return 0; done
    return 1
}

for v in "${views[@]}"; do
    if in_list "$v" "${statuses[@]}" || in_list "$v" "${mentioned[@]}"; then
        fail view-stored-as-status "$v is a view over a requested action and is never stored"
    fi
done
for s in "${mentioned[@]}"; do
    in_list "$s" "${statuses[@]}" || fail unknown-status "$s is not in statuses"
done

while read -r t; do
    n=$(T=$t yq '[.transitions[] | select(.from == strenv(T))] | length' "$file")
    [ "$n" -eq 0 ] || fail transition-out-of-terminal "$t is terminal and has $n transitions out"
done < <(yq '.terminal[]' "$file")

ambiguous=$(yq '.projection | to_entries | .[] | select((.value | length) > 1) | select(.value | map(has("when")) | all | not) | .key' "$file" | head -n1)
[ -z "$ambiguous" ] || fail ambiguous-projection "$ambiguous sets more than one status without a when condition on each"

echo "ok $file"
