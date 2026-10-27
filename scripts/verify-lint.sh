#!/usr/bin/env bash
# Map lint for every .claude/skills/verify-<domain>/ skill:
# four H2 sections per feature file, scripted features have an executable script,
# the SKILL.md status table matches the feature files, and every recipe and
# contract file is referenced by a feature file or listed in verify-allowlist.txt.
set -euo pipefail
cd "$(dirname "$0")/.."
rc=0
err() {
  echo "verify-lint: $*" >&2
  rc=1
}

recipes=$(just --summary)
for skill in .claude/skills/verify-*/; do
  skill=${skill%/}
  domain=${skill##*/verify-}
  [ -f "$skill/SKILL.md" ] || err "$skill: SKILL.md missing"
  refs="$skill/features/*.md $skill/verify-allowlist.txt"

  for f in "$skill"/features/F[0-9]*.md; do
    id=$(basename "$f" .md)
    h2=$(grep '^## ' "$f" | sed 's/^## //')
    [ "$(sed -n 1p <<<"$h2")" = Sub-features ] &&
      [ "$(sed -n 2p <<<"$h2")" = 'How to get to it (user POV)' ] &&
      [[ "$(sed -n 3p <<<"$h2")" == 'Driving it with '* ]] &&
      [ "$(sed -n 4p <<<"$h2")" = Gotchas ] &&
      [ "$(wc -l <<<"$h2")" = 4 ] || err "$f: needs exactly the four H2 sections in order"

    status=$(sed -n '2,4p' "$f" | grep -o 'Status: [a-z]*' | head -1 | sed 's/Status: //')
    case $status in
      scripted) [ -x "scripts/verify/$domain/$id.sh" ] || err "$f: scripted but scripts/verify/$domain/$id.sh is not executable" ;;
      manual | planned) ;;
      *) err "$f: Status must be scripted, manual or planned" ;;
    esac
    grep -Eq "^\| ${id%%-*} .*\| $status \|\$" "$skill/SKILL.md" || err "$skill/SKILL.md: status row for ${id%%-*} does not say $status"
  done

  # shellcheck disable=SC2086
  for r in $recipes; do
    grep -Eqh "just $r\b|^$r:" $refs || err "recipe $r is not referenced by a feature or $skill/verify-allowlist.txt"
  done
  if [ -d contracts ]; then
    while IFS= read -r c; do
      # shellcheck disable=SC2086
      grep -Fqh "$c" $refs || err "contract file $c is not referenced by a feature or $skill/verify-allowlist.txt"
    done < <(find contracts -type f)
  fi
done
exit "$rc"
