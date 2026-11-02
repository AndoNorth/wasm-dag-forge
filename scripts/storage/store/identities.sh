#!/usr/bin/env bash
# The identities of the object store: one credential per role of contracts/store/roles.yaml, generated on first use into
# the state directory of the cluster scripts and never committed.
#   render  print the Secret manifest holding the identities file of the store (default)
#   path    print the path of the credentials file that the contract script reads
# Usage: identities.sh [render|path]. Exit 0 ok, 2 usage.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

mode=${1:-render}
case $mode in render | path) ;; *) echo "usage: identities.sh [render|path]" >&2; exit 2 ;; esac

state=${WDF_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/wasm-dag-forge}
file=$state/objectstore.credentials.json

if [ ! -s "$file" ]; then
    mkdir -p "$state"
    tmp=$(umask 077 && mktemp "$file.XXXXXX")
    uv run --frozen --quiet python - "$tmp" <<'PY'
import sys

from wdf_storecheck.provision import generate_credentials
from wdf_storecheck.s3 import dump_credentials

dump_credentials(sys.argv[1], generate_credentials())
PY
    mv "$tmp" "$file"
fi

[ "$mode" = path ] && { echo "$file"; exit 0; }

uv run --frozen --quiet python - "$file" <<'PY'
import json
import sys

from wdf_storecheck.provision import seaweedfs_config
from wdf_storecheck.s3 import load_credentials

identities = json.dumps(seaweedfs_config(load_credentials(sys.argv[1])))
print(json.dumps({
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": "objectstore-identities", "namespace": "wdf-infra"},
    "type": "Opaque",
    "stringData": {"s3.json": identities},
}))
PY
