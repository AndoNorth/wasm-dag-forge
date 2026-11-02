"""The library surface that is not implemented yet, raising until the owning lane delivers it."""

from typing import Any

OWNER = "E"
# Functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED = ("chunk_count",)


def _not_implemented() -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {OWNER}")


def chunk_count(conn: Any) -> int:
    raise _not_implemented()
