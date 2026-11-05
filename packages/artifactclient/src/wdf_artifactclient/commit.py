"""Commit by reference: what the workflow's record activity calls for an accepted attempt."""

from typing import Any

from ._stub import not_implemented

STUBBED = ("Committed", "commit")


class Committed:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("C")


def commit(
    conn: Any,
    client: Any,
    references: list[Any],
    job_id: str,
    task: str,
    accepted_attempt: str | None = None,
) -> Committed:
    raise not_implemented("C")
