# shellcheck shell=bash
# Sourced by the F<n>-*.sh feature scripts. Env from run.sh: TREE, EVID_F, RUN_DIR.
: "${TREE:?}" "${EVID_F:?}" "${RUN_DIR:?}"
CHECKS=$EVID_F/checks.txt
: >>"$CHECKS"

# Run a command in the exported tree and append it to the evidence files.
# Sets RC, LAST_OUT and LAST_ERR.
cap() {
  local id=$1
  shift
  LAST_OUT=$RUN_DIR/last.out
  LAST_ERR=$RUN_DIR/last.err
  echo "### $id: $*" >>"$EVID_F/cmd.txt"
  set +e
  (cd "$TREE" && "$@") >"$LAST_OUT" 2>"$LAST_ERR"
  RC=$?
  set -e
  { echo "### $id (exit $RC)"; cat "$LAST_OUT"; } >>"$EVID_F/stdout.txt"
  { echo "### $id (exit $RC)"; cat "$LAST_ERR"; } >>"$EVID_F/stderr.txt"
}

# Run a command inside the tree's dev shell.
nd() { nix develop "$TREE" -c "$@"; }

ok() { echo "pass $1: $2" | tee -a "$CHECKS"; }
bad() {
  echo "fail $1: $2" | tee -a "$CHECKS"
  FAILED=1
}
skipc() { echo "skip $1: $2" | tee -a "$CHECKS"; }

# Feature exit status: 0 unless a check failed.
finish() { [ -z "${FAILED:-}" ]; }

# is <id> <pass detail> <fail detail> <test cmd...>
is() {
  local id=$1 okm=$2 badm=$3
  shift 3
  if "$@"; then ok "$id" "$okm"; else bad "$id" "$badm"; fi
}
