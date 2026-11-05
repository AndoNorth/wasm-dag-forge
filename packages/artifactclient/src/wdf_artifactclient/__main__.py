"""Usage: python -m wdf_artifactclient verify --endpoint URL --credentials FILE --role ROLE REF"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("C")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
