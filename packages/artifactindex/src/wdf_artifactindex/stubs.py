"""The library surface of the package, raising until the owning lane implements each function."""

from typing import Any

OWNER = "A"
# Tables and functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED = (
    "artifact",
    "index_reference",
    "mark_deleted",
    "references_to",
    "record_cache_entry",
    "touch_cache_entry",
)


def _not_implemented() -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {OWNER}")


def index_reference(conn: Any, reference: Any) -> Any:
    raise _not_implemented()


def mark_deleted(conn: Any, uri: str, at: Any) -> Any:
    raise _not_implemented()


def references_to(conn: Any, uri: str) -> Any:
    raise _not_implemented()


def record_cache_entry(conn: Any, entry: Any) -> Any:
    raise _not_implemented()


def touch_cache_entry(conn: Any, key: str, at: Any) -> None:
    raise _not_implemented()
