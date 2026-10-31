#!/usr/bin/env bash
# Metrics loader: validates one metrics file against the naming rule and the label policy of README.md.
# Usage: check.sh <metrics.yaml>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
# shellcheck source=/dev/null
source "$root/scripts/verify/cluster/lib/tools.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <metrics.yaml>" >&2; exit 2; }
file=$1
schema=$here/metrics.schema.json

fail() {
    echo "reason: $1: $2" >&2
    exit 1
}

out=$(check-jsonschema --schemafile "$schema" "$file" 2>&1) ||
    fail schema-violation "$(tail -n +2 <<<"$out" | head -n3 | tr '\n' ' ')"

mapfile -t denied < <(yq -p json -o yaml '."x-label-denylist"[]' "$schema")

while read -r name; do
    type=$(N=$name yq '.metrics[strenv(N)].type' "$file")
    unit=$(N=$name yq '.metrics[strenv(N)].unit // ""' "$file")
    [[ $name =~ ^wdf_[a-z][a-z0-9]*(_[a-z0-9]+)+$ ]] || fail bad-metric-name "$name is not wdf_ and snake case"
    if [ "$type" = counter ]; then
        [[ $name == *_total ]] || fail bad-metric-name "counter $name does not end in _total"
        stem=${name%_total}
    else
        [[ $name != *_total ]] || fail bad-metric-name "$type $name ends in _total"
        stem=$name
    fi
    [ -z "$unit" ] || [[ $stem == *_"$unit" ]] || fail bad-metric-name "$name has the unit $unit and does not end in _$unit"
    [ "$type" != histogram ] || [ "$(N=$name yq '.metrics[strenv(N)] | has("buckets")' "$file")" = true ] ||
        fail bad-metric-name "histogram $name has no buckets"

    while read -r label; do
        [ -n "$label" ] || continue
        [[ " ${denied[*]} " != *" $label "* ]] || fail denied-label "$name has the label $label, an unbounded identifier"
        has=$(N=$name L=$label yq '.metrics[strenv(N)].labels[strenv(L)] | (has("values") or has("values_from"))' "$file")
        [ "$has" = true ] || fail undeclared-label-values "$name label $label declares neither values nor values_from"
        from=$(N=$name L=$label yq '.metrics[strenv(N)].labels[strenv(L)].values_from // ""' "$file")
        [ -z "$from" ] || [ -d "$root/$from" ] || fail unknown-values-source "$name label $label takes its values from $from, which is not a directory"
    done < <(N=$name yq '.metrics[strenv(N)].labels | keys | .[]' "$file")
done < <(yq '.metrics | keys | .[]' "$file")

echo "ok $file"
