"""Usage: python -m wdf_artifactclient.faults delete|flip-byte|corrupt-checkpoint --endpoint URL
--credentials FILE --role ROLE --uri URI"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("F")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
