#!/usr/bin/env bash
# The credential of the app database: generated on first use into the state directory of the cluster scripts, shown
# as a Kubernetes Secret for the Tilt module and as a bare value for the host-side helpers. Never committed.
#   render    print the Secret manifest (default)
#   password  print the credential
# Usage: secrets.sh [render|password]. Exit 0 ok, 2 usage.
set -euo pipefail

state=${WDF_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/wasm-dag-forge}
file=$state/appdb.credential
mode=${1:-render}

case $mode in render | password) ;; *) echo "usage: secrets.sh [render|password]" >&2; exit 2 ;; esac

if [ ! -s "$file" ]; then
    mkdir -p "$state"
    (umask 077 && head -c 32 /dev/urandom | base64 | tr -d '=+/\n' >"$file")
fi

value=$(cat "$file")
case $mode in
    password) echo "$value" ;;
    render)
        cat <<SECRET
apiVersion: v1
kind: Secret
metadata:
  name: appdb-credentials
  namespace: wdf-infra
type: Opaque
stringData:
  POSTGRES_PASSWORD: $value
SECRET
        ;;
esac
