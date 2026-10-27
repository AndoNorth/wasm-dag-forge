#!/usr/bin/env bash
# Usage: run.sh [--dirty] [F1 F2 ...]   (no feature ids runs every feature)
# Launch, doctor, drive, evidence, cleanup. Evidence lands in
# <repo>/.verify-evidence/dev-environment/<run id>/ and survives cleanup.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
skill=$root/.claude/skills/verify-dev-environment
lock=/tmp/wdf-verify-dev-environment.lock

dirty=
want=()
for a in "$@"; do
  case $a in
    --dirty) dirty=--dirty ;;
    F[0-9]*) want+=("$a") ;;
    *)
      echo "usage: run.sh [--dirty] [F1 F2 ...]" >&2
      exit 2
      ;;
  esac
done

run_id=$(date -u +%Y%m%dT%H%M%SZ)-${WDF_SLOT:-0}
run_dir=${WDF_VERIFY_TMP:-/tmp}/wdf-verify-dev-environment/$run_id
evid=$root/.verify-evidence/dev-environment/$run_id
mkdir -p "$run_dir" "$evid"

"$here/doctor.sh" | tee "$evid/doctor-pre.txt" || {
  rm -rf "$run_dir"
  exit 1
}
exec 9>"$lock"
flock -n 9 || {
  echo "fail lock: $lock is held by another run" >&2
  rm -rf "$run_dir"
  exit 1
}
trap '"$here/clean.sh" "$run_dir"' EXIT

"$here/export.sh" "$run_dir" $dirty >"$evid/launch.txt" 2>&1 || {
  cat "$evid/launch.txt" >&2
  exit 1
}
WDF_VERIFY_LOCK_OWNED=1 "$here/doctor.sh" "$run_dir" | tee "$evid/doctor.txt"

meta() { sed -n '2,4p' "$1" | grep -o "$2: [^|]*" | head -1 | sed "s/^$2: *//; s/ *$//"; }
esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' '; }

rows=
failed=0
for f in "$skill"/features/F[0-9]*.md; do
  base=$(basename "$f" .md)
  id=${base%%-*}
  if [ ${#want[@]} -gt 0 ] && ! printf '%s\n' "${want[@]}" | grep -qx "$id"; then continue; fi
  status=$(meta "$f" Status)
  needs=$(meta "$f" Needs || true)
  fe=$evid/$id
  mkdir -p "$fe"
  result=planned
  rc=
  skips=
  if [ "$status" = scripted ]; then
    echo "== $id"
    set +e
    TREE=$run_dir/tree EVID_F=$fe RUN_DIR=$run_dir bash "$here/$base.sh"
    rc=$?
    set -e
    echo "$rc" >"$fe/exit"
    result=pass
    [ "$rc" = 0 ] || {
      result=fail
      failed=1
    }
    while IFS= read -r l; do skips="$skips${skips:+,}\"$(esc "${l#skip }")\""; done < <(grep '^skip ' "$fe/checks.txt" || true)
  else
    echo "== $id planned${needs:+: $needs}"
    skips="\"$(esc "planned${needs:+: $needs}")\""
  fi
  rows="$rows${rows:+,}{\"id\":\"$id\",\"tier\":\"$(esc "$(meta "$f" Tier)")\",\"result\":\"$result\",\"exit\":${rc:-null},\"skips\":[$skips]}"
done

cat >"$evid/summary.json" <<JSON
{"run_id":"$run_id","commit":"$(cat "$run_dir/commit")","tree":"${dirty:+dirty}${dirty:-committed}","features":[$rows]}
JSON
echo "evidence: $evid"
exit "$failed"
