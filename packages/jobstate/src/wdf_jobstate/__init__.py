"""The accepted-jobs table: row model, schema generator and the guarded library."""

from .jobs import (
    ControlRequest,
    Duplicate,
    IllegalTransition,
    InsertResult,
    JobNotFound,
    Lost,
    Moved,
    Vocabulary,
    get_job,
    insert_accepted,
    mark_delivered,
    mark_published,
    request_action,
    status_vocabulary,
    transition,
)
from .models import JobRow

# Tables and functions that are not implemented yet, each emptied by the lane that delivers it.
STUBBED: tuple[str, ...] = ()

__all__ = [
    "STUBBED",
    "ControlRequest",
    "Duplicate",
    "IllegalTransition",
    "InsertResult",
    "JobNotFound",
    "JobRow",
    "Lost",
    "Moved",
    "Vocabulary",
    "get_job",
    "insert_accepted",
    "mark_delivered",
    "mark_published",
    "request_action",
    "status_vocabulary",
    "transition",
]
