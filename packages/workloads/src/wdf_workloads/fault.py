"""The fault workload: each mode fails the way that produces one exit class."""

from pathlib import Path

from ._stub import not_implemented

STUBBED = ("fault",)


def fault(seed: int, args: dict, out: Path) -> None:
    raise not_implemented("W")
