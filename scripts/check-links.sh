#!/usr/bin/env bash
# Fails on a relative link in tracked Markdown whose target does not exist. Links in code spans and fences are ignored.
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC2016
git ls-files -z '*.md' | python3 -c '
import os, re, sys

link = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
dead = 0
for path in sys.stdin.read().split("\0"):
    if not path or not os.path.exists(path) or os.path.islink(path):
        continue
    fenced = False
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        for target in link.findall(re.sub(r"`[^`]*`", "", line)):
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            file = target.split("#")[0]
            if not os.path.exists(os.path.join(os.path.dirname(path), file)):
                print(f"{path}:{n}: dead link {target}")
                dead += 1
sys.exit(1 if dead else 0)
'
