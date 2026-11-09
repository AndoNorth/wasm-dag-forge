"""One aggregate over the results named by an aggregate index, read in index order."""

from pathlib import Path
from typing import Any

from ._stub import not_implemented

STUBBED = ("run",)


def run(
    client: Any, index_reference: Any, tenant: str, aggregation: str, args: dict, out_dir: Path
):
    raise not_implemented("A")
