"""The blocks a workload must write for (seed, args), and their digest over parsed values."""

from ._stub import not_implemented

STUBBED = ("expected_blocks", "expected_digest")


def expected_blocks(workload: str, seed: int, args: dict):
    raise not_implemented("K")


def expected_digest(workload: str, seed: int, args: dict) -> str:
    raise not_implemented("K")
