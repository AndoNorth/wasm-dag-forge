"""The library surface of the package, raising until the owning lane implements each function."""

from typing import Any

OWNER = "J"
# Tables and functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED = (
    "accepted_job",
    "insert_accepted",
    "get_job",
    "transition",
    "request_action",
    "mark_published",
    "mark_delivered",
    "status_vocabulary",
)


def _not_implemented() -> NotImplementedError:
    return NotImplementedError(f"not implemented: owned by lane {OWNER}")


def insert_accepted(conn: Any, rows: list[Any]) -> Any:
    raise _not_implemented()


def get_job(conn: Any, job_id: str) -> Any:
    raise _not_implemented()


def transition(conn: Any, job_id: str, expected: str, to: str, row_only: bool = False) -> Any:
    raise _not_implemented()


def request_action(conn: Any, job_id: str, action: str) -> Any:
    raise _not_implemented()


def mark_published(conn: Any, job_id: str) -> None:
    raise _not_implemented()


def mark_delivered(conn: Any, job_id: str, control_seq: int) -> None:
    raise _not_implemented()


def status_vocabulary() -> Any:
    raise _not_implemented()
