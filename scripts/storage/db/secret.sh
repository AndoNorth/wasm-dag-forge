#!/usr/bin/env bash
# Prints a Secret manifest holding a generated password, creating the password on first use.
# The password lives in the state directory of the cluster scripts and never in the repository.
# Usage: secret.sh <name> <namespace>. Exit 0 manifest printed, 2 usage.
set -euo pipefail

[ $# -eq 2 ] || { echo "usage: secret.sh <name> <namespace>" >&2; exit 2; }
name=$1
namespace=$2
state=${WDF_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/wasm-dag-forge}
file=$state/$name.password

if [ ! -s "$file" ]; then
    mkdir -p "$state"
    (umask 077 && head -c 24 /dev/urandom | base64 | tr -d '=+/\n' >"$file")
fi

cat <<MANIFEST
apiVersion: v1
kind: Secret
metadata:
  name: $name
  namespace: $namespace
type: Opaque
stringData:
  POSTGRES_PASSWORD: $(cat "$file")
MANIFEST
