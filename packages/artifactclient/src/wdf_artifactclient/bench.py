"""Usage: python -m wdf_artifactclient.bench --results N [--twice] [--endpoint URL]"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("K")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
