"""Usage: python -m wdf_runtime run --component C --input input.json --out DIR [--memory-mib N]
                                 [--timeout-s S] [--cache-dir D]

Prints "class: <class>" and "heartbeats: <n>". Exit 0 for ok, 1 for any other class, 2 usage.
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("R")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
