#!/usr/bin/env bash
# Artifact reference loader: validates one reference and checks that its uri is a key of its kind's template.
# Usage: check.sh <reference file>. Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$here/../../scripts/verify/cluster/lib/tools.sh"

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: check.sh <reference.yaml or reference.json>" >&2; exit 2; }
file=$1
schema=$here/reference.schema.json
layout=$here/layout.yaml

fail() {
    echo "reason: $1: $2" >&2
    exit 1
}

# A reference holds no field beyond the schema's, so no field can hold bytes.
mapfile -t allowed < <(yq -p json '.properties | keys | .[]' "$schema")
while read -r field; do
    [[ " ${allowed[*]} " == *" $field "* ]] || fail unknown-field "$field is not a field of a reference"
done < <(yq 'keys | .[]' "$file")

out=$(check-jsonschema --schemafile "$schema" "$file" 2>&1) ||
    fail schema-violation "$(tail -n +2 <<<"$out" | head -n3 | tr '\n' ' ')"

kind=$(yq '.kind' "$file")
sha=$(yq '.sha256' "$file")
uri=$(yq '.uri' "$file")
rest=${uri#s3://}
bucket=${rest%%/*}
key=${rest#*/}

want=$(K=$kind yq '.kinds[strenv(K)].bucket' "$layout")
[ "$bucket" = "$want" ] || fail wrong-bucket "kind $kind lives in $want, the uri names $bucket"
class=$(K=$kind yq '.kinds[strenv(K)].class' "$layout")
template=$(K=$kind yq '.kinds[strenv(K)].key' "$layout")
IFS=/ read -ra seg <<<"$key"
IFS=/ read -ra tpl <<<"$template"

if [ "$class" = content-addressed ]; then
    [ "${#seg[@]}" -eq "${#tpl[@]}" ] && [ "${seg[0]}" = sha256 ] || fail key-template-mismatch "$key does not match $template"
    [ "${seg[1]}" = "${sha:0:2}" ] || fail bad-hash-prefix "key prefix ${seg[1]} is not the first two hex digits of $sha"
    [ "${seg[2]}" = "$sha" ] || fail hash-mismatch "key hash ${seg[2]} is not the sha256 of the reference"
    echo "ok $file"
    exit 0
fi

# An attempt-scoped key repeats the producer and the name, and the attempt is a segment of its own.
attempt=$(yq '.producer.attempt' "$file")
if [ "${#seg[@]}" -ne "${#tpl[@]}" ]; then
    [[ " ${seg[*]} " == *" $attempt "* ]] || fail missing-attempt-segment "$key has no segment for attempt $attempt"
    fail key-template-mismatch "$key has ${#seg[@]} segments, $template has ${#tpl[@]}"
fi
declare -A expect=([job]=$(yq '.producer.job' "$file") [task]=$(yq '.producer.task' "$file") [attempt]=$attempt [name]=$(yq '.name' "$file"))
for i in "${!tpl[@]}"; do
    part=${tpl[$i]}
    [ -n "${seg[$i]}" ] || fail key-template-mismatch "segment $((i + 1)) of $key is empty"
    if [[ $part =~ ^\<(job|task|attempt|name)\>$ ]]; then
        want=${expect[${BASH_REMATCH[1]}]}
        [ "${seg[$i]}" = "$want" ] || fail key-template-mismatch "segment $((i + 1)) of $key is ${seg[$i]}, the reference says $want"
    elif [[ $part != \<* ]]; then
        [ "${seg[$i]}" = "$part" ] || fail key-template-mismatch "segment $((i + 1)) of $key is ${seg[$i]}, the template says $part"
    fi
done
echo "ok $file"
