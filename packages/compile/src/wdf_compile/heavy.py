"""Usage: python -m wdf_compile.heavy --size-mib N --out DIR

Writes one bundle with one generated module of about N MiB of source.
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("C")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
