"""Usage: python -m wdf_kernel.check summary --summary summary.json --inputs TASK=A.json...
                                   [--missing TASK...]

The summary an aggregation must write over its inputs, and the check of a summary.json against it.
"""

from ._stub import not_implemented

STUBBED = ("expected_summary", "main")


def expected_summary(aggregation: str, args: dict, inputs: dict):
    raise not_implemented("K")


def main(argv: list[str]) -> int:
    raise not_implemented("K")
