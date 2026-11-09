"""Usage: python -m wdf_compile build --bundle B.tar --entry MODULE --out DIR [--find-links URL]
                                    [--timeout S]
python -m wdf_compile info B.tar

Exit codes from the compile pool of contracts/workload/exit-classes.yaml.
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("C")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
