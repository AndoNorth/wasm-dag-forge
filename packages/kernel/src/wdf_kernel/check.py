"""Usage: python -m wdf_kernel.check results --input input.json --results results.xml [--max-rows N]
python -m wdf_kernel.check summary --summary summary.json --inputs TASK=A.json...
                                   [--missing TASK...]

Exit 0 equal, 1 not equal, 2 usage. Prints "digest: <hex>".
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("K")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
