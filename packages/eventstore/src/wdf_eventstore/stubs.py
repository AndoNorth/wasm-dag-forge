"""The library surface of the package, raising until the owning lane implements each function."""

from typing import Any

OWNER = "E"
# Tables and functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED = ("event", "insert_events", "events_for_job", "chunk_count")


def _not_implemented() -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {OWNER}")


def insert_events(conn: Any, events: list[Any]) -> Any:
    raise _not_implemented()


def events_for_job(conn: Any, job_id: str) -> Any:
    raise _not_implemented()


def chunk_count(conn: Any) -> int:
    raise _not_implemented()
