"""The artifact layout, read from the contract file that ships inside the package."""

from functools import cache
from typing import Any

import yaml

from .profile import CONTRACT


@cache
def layout() -> dict[str, Any]:
    """The buckets and the kinds of contracts/artifact/layout.yaml, parsed once.

    The result is shared, so a caller that edits it works on a deep copy.
    """
    return yaml.safe_load((CONTRACT / "layout.yaml").read_text())
