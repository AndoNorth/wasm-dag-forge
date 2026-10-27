#!/usr/bin/env bash
# Usage: lint.sh | lint.sh --expect-fail <file> | lint.sh --self-test
set -euo pipefail
cd "$(dirname "$0")/.."

FIXTURES=scripts/fixtures/lint-bad

run_ruff()        { ruff check "$@" && ruff format --check "$@"; }
run_yamllint()    { yamllint --strict "$@"; }
run_shellcheck()  { shellcheck "$@"; }
run_actionlint()  { actionlint "$@"; }
run_kubeconform() { kubeconform -strict -summary "$@"; }
run_nixfmt()      { nixfmt --check "$@"; }

# Tracked and untracked-but-not-ignored files matching the patterns, minus the bad fixtures.
files() {
    git ls-files -z --cached --others --exclude-standard -- "$@" | tr '\0' '\n' | grep -v "^$FIXTURES/" || true
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
    local rc=0
    step() {
        local name=$1 fn=$2
        shift 2
        mapfile -t f < <(files "$@")
        if [ ${#f[@]} -eq 0 ]; then echo "skip $name: no files"; return; fi
        echo "== $name (${#f[@]} files)"
        "$fn" "${f[@]}" || rc=1
    }
    step ruff        run_ruff        '*.py'
    step yamllint    run_yamllint    '*.yaml' '*.yml'
    step shellcheck  run_shellcheck  '*.sh'
    step actionlint  run_actionlint  '.github/workflows/*.yml' '.github/workflows/*.yaml'
    # deploy/versions.yaml is a pins file, not a Kubernetes manifest.
    mapfile -t f < <(files 'deploy/*.yaml' 'deploy/*.yml' | grep -v '^deploy/versions\.yaml$' || true)
    if [ ${#f[@]} -gt 0 ]; then echo "== kubeconform (${#f[@]} files)"; run_kubeconform "${f[@]}" || rc=1; else echo "skip kubeconform: no files"; fi
    step nixfmt      run_nixfmt      '*.nix'
    return $rc
}

case "${1:-}" in
    --expect-fail) [ $# -eq 2 ] || { echo "usage: lint.sh --expect-fail <file>" >&2; exit 2; }; expect_fail "$2" ;;
    --self-test)
        rc=0
        fixtures=("$FIXTURES"/*)
        for f in "${fixtures[@]}"; do expect_fail "$f" || rc=1; done
        [ ${#fixtures[@]} -eq 6 ] || { echo "expected 6 fixtures" >&2; rc=1; }
        exit $rc ;;
    "") lint_all ;;
    *) echo "usage: lint.sh [--expect-fail <file> | --self-test]" >&2; exit 2 ;;
esac
