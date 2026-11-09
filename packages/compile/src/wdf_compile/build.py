"""The build procedure: unpack, install the locked dependencies, componentize and check."""

from pathlib import Path

from ._stub import not_implemented

STUBBED = ("build",)


def build(
    bundle: Path,
    entry: str,
    out: Path,
    find_links: str | None = None,
    timeout_s: float | None = None,
):
    raise not_implemented("C")
