#!/usr/bin/env bash
# Usage: lint.sh | lint.sh --expect-fail <file> | lint.sh --self-test | lint.sh --verify-skills
set -euo pipefail
cd "$(dirname "$0")/.."

FIXTURES=scripts/fixtures/lint-bad

run_ruff()        { ruff check "$@" && ruff format --check "$@"; }
run_yamllint()    { yamllint --strict "$@"; }
run_shellcheck()  { shellcheck "$@"; }
run_actionlint()  { actionlint "$@"; }
run_kubeconform() { kubeconform -strict -summary "$@"; }
run_nixfmt()      { nixfmt --check "$@"; }

# Every candidate file: tracked and untracked-but-not-ignored in a git checkout, found on disk otherwise (an export).
all_files() {
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git ls-files -z --cached --others --exclude-standard
    else
        find . \( -name .git -o -name .venv -o -name node_modules -o -name .verify-evidence -o -name result \) -prune \
            -o -type f -print0 | sed -z 's|^\./||'
    fi
}

# Candidate files matching the glob patterns, minus the bad fixtures.
files() {
    local p g
    while IFS= read -r -d '' p; do
        for g in "$@"; do
            # shellcheck disable=SC2254
            case $p in $g) printf '%s\n' "$p"; break ;; esac
        done
    done < <(all_files) | grep -v "^$FIXTURES/" || true
}

# Linter named by the fixture's basename stem (ruff.py -> ruff).
expect_fail() {
    local file=$1 stem
    stem=$(basename "$file")
    stem=${stem%.*}
    declare -F "run_$stem" >/dev/null || { echo "no linter for fixture: $file" >&2; return 2; }
    if "run_$stem" "$file" >/dev/null 2>&1; then
        echo "FAIL: $stem accepted $file" >&2
        return 1
    fi
    echo "ok: $stem rejects $file"
}

lint_all() {
    local -a f
    local rc=0 linted=0
    step() {
        local name=$1 fn=$2
        shift 2
        mapfile -t f < <(files "$@")
        if [ ${#f[@]} -eq 0 ]; then echo "skip $name: no files"; return; fi
        echo "== $name (${#f[@]} files)"
        linted=$((linted + ${#f[@]}))
        "$fn" "${f[@]}" || rc=1
    }
    step ruff        run_ruff        '*.py'
    step yamllint    run_yamllint    '*.yaml' '*.yml'
    step shellcheck  run_shellcheck  '*.sh'
    step actionlint  run_actionlint  '.github/workflows/*.yml' '.github/workflows/*.yaml'
    # deploy/versions.yaml, deploy/presets/ and the module manifests hold pins, presets and module shapes, not Kubernetes manifests.
    mapfile -t f < <(files 'deploy/*.yaml' 'deploy/*.yml' | grep -Ev '^deploy/(versions\.yaml|presets/|.*/module\.yaml$)' || true)
    if [ ${#f[@]} -gt 0 ]; then echo "== kubeconform (${#f[@]} files)"; linted=$((linted + ${#f[@]})); run_kubeconform "${f[@]}" || rc=1; else echo "skip kubeconform: no files"; fi
    step nixfmt      run_nixfmt      '*.nix'
    if [ "$linted" -eq 0 ] && [ -n "$(all_files | tr -d '\0')" ]; then
        echo "error: no files linted in a tree that has files, refusing to report success" >&2
        rc=1
    fi
    return $rc
}

# Each verify-<domain> skill needs a SKILL.md whose frontmatter name matches its directory.
verify_skills() {
    local d name rc=0 n=0
    for d in .claude/skills/verify-*/; do
        [ -d "$d" ] || continue
        n=$((n + 1))
        name=$(basename "$d")
        if ! grep -qx "name: $name" "$d/SKILL.md" 2>/dev/null; then
            echo "error: $d/SKILL.md missing or its name is not $name" >&2
            rc=1
        fi
    done
    echo "verify-lint: $n skills checked"
    return $rc
}

case "${1:-}" in
    --verify-skills) verify_skills ;;
    --expect-fail) [ $# -eq 2 ] || { echo "usage: lint.sh --expect-fail <file>" >&2; exit 2; }; expect_fail "$2" ;;
    --self-test)
        rc=0
        fixtures=("$FIXTURES"/*)
        for f in "${fixtures[@]}"; do expect_fail "$f" || rc=1; done
        [ ${#fixtures[@]} -eq 6 ] || { echo "expected 6 fixtures" >&2; rc=1; }
        exit $rc ;;
    "") lint_all ;;
    *) echo "usage: lint.sh [--expect-fail <file> | --self-test | --verify-skills]" >&2; exit 2 ;;
esac
