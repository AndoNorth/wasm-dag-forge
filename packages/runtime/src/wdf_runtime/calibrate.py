"""Usage: python -m wdf_runtime.calibrate --component C [--runs 3] [--check CATALOGUE]

Prints "units_per_second: <rate>". With --check, exit 1 when the rate is outside the tolerance of
the catalogue.
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("R")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
