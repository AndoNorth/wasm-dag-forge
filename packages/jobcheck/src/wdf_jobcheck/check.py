"""Command line of contracts/job/check.sh: python -m wdf_jobcheck.check <document.json>.

Validates one document with the `ci` profile and the live slots files.
Exit 0 valid, 1 invalid with one `reason: <code>: <detail>` line per issue on stderr, 2 usage,
3 when a pass is still a stub.
"""

import sys
from pathlib import Path

from wdf_jobdoc.profile_files import load_profile

from . import validate

REPO_ROOT = Path(__file__).resolve().parents[4]


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print("usage: check.sh <document.json>", file=sys.stderr)
        return 2
    profile = load_profile("ci", REPO_ROOT)
    try:
        report = validate(Path(argv[0]).read_bytes(), profile)
    except NotImplementedError as exc:
        print(exc, file=sys.stderr)
        return 3
    for issue in report.issues:
        print(f"reason: {issue.code}: {issue.path or '/'} {issue.message}", file=sys.stderr)
    if not report.ok:
        return 1
    print(f"ok {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
