#!/usr/bin/env bash
# Fails when a directory under contracts/ has no row in the map table of docs/contracts.md, or a row points at a path that does not exist.
# Roles, owners, consumers and the diagram are not checked. WDF_CONTRACTS_ROOT points at another tree, which the self-test uses.
set -euo pipefail
root=${WDF_CONTRACTS_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
doc=$root/docs/contracts.md
[ -f "$doc" ] || { echo "error: $doc not found" >&2; exit 1; }

# The first link of the first cell of each row in the "## Map" table, as a path under contracts/.
rows=$(awk '/^## /{in_map = ($0 == "## Map")} in_map && /^\| \[/' "$doc" \
    | sed -n 's/^| \[[^]]*\](\.\.\/contracts\/\([^)#]*\)).*/\1/p' | sed 's|/README\.md$||; s|/$||')

rc=0
for dir in "$root"/contracts/*/; do
    name=$(basename "$dir")
    grep -qx "$name" <<<"$rows" || {
        echo "error: contracts/$name has no row in the map table of docs/contracts.md, add its row and update docs/architecture/15-contracts.drawio" >&2
        rc=1
    }
done
while IFS= read -r path; do
    [ -z "$path" ] || [ -e "$root/contracts/$path" ] || {
        echo "error: the map table of docs/contracts.md has a row for contracts/$path, which does not exist, remove the row and update docs/architecture/15-contracts.drawio" >&2
        rc=1
    }
done <<<"$rows"
exit $rc
