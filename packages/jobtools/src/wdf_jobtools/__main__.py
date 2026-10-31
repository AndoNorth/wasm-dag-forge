"""python -m wdf_jobtools validate|plan|patch-check: exit 3 while a command is a stub."""

import sys

COMMANDS = ("validate", "plan", "patch-check")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: python -m wdf_jobtools {{{','.join(COMMANDS)}}} ...", file=sys.stderr)
        return 2
    print("not implemented: owned by lane L", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
