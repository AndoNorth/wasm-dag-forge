"""Command line loader of contracts/events/check.sh: python -m wdf_events.check <event.json>."""

import sys
from pathlib import Path

from .codec import from_bytes
from .errors import EventError


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print("usage: check.sh <event.json>", file=sys.stderr)
        return 2
    try:
        from_bytes(Path(argv[0]).read_bytes())
    except EventError as exc:
        print(f"reason: {exc.reason}: {exc.detail}", file=sys.stderr)
        return 1
    print(f"ok {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
