"""Usage: python -m wdf_aggregate run --index REF.json --tenant T --aggregation NAME --args JSON
                                   --out DIR --endpoint URL --credentials FILE --role aggregate
python -m wdf_aggregate files --aggregation NAME --args JSON --out DIR [--missing TASK...]
                              TASK=RESULTS...

Exit codes from the aggregate pool of contracts/workload/exit-classes.yaml.
"""

import sys

from ._stub import not_implemented

STUBBED = ("main",)


def main(argv: list[str]) -> int:
    raise not_implemented("A")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
