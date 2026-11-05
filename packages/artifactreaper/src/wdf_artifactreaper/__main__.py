"""Usage: python -m wdf_artifactreaper run-once --dsn DSN --endpoint URL --credentials FILE
python -m wdf_artifactreaper schedule ensure|trigger|worker --temporal-address ADDR
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("P")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
