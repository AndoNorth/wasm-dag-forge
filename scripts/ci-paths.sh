#!/usr/bin/env bash
# Reads changed paths, one per line, and prints docs when every one is documentation, full otherwise.
# Documentation is Markdown outside the code trees, plus anything under docs/ except Python, which pytest would collect.
# Markdown under contracts/ and packages/ stays full because the unit tests read those READMEs.
set -euo pipefail

verdict=full
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case $path in
        contracts/* | deploy/* | packages/* | scripts/* | nix/* | just/* | .github/*) echo full; exit ;;
        flake.nix | flake.lock | justfile) echo full; exit ;;
        docs/*.py) echo full; exit ;;
        docs/* | *.md) verdict=docs ;;
        *) echo full; exit ;;
    esac
done
echo "$verdict"
