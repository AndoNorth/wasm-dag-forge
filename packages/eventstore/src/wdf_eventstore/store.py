"""Idempotent batch insert and the read of one job's events."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from wdf_events import Event, to_event_row

from .models import EventRow

# The ingest rates of three batch sizes were within 6 percent of each other on the measured host.
BATCH_SIZE = 5_000

INSERT = """
INSERT INTO event (event_id, event_time, type, source, tenant, job_id, task_id, attempt, body)
SELECT * FROM unnest(
    %s::text[], %s::timestamptz[], %s::text[], %s::text[], %s::text[],
    %s::uuid[], %s::text[], %s::integer[], %s::text[]
)
ON CONFLICT (event_id, event_time) DO NOTHING
"""

SELECT_JOB = """
SELECT event_id, event_time, type, source, tenant, job_id::text, task_id, attempt, body
FROM event WHERE job_id = %s::uuid ORDER BY event_time, event_id
"""


@dataclass(frozen=True)
class BatchResult:
    inserted: int
    duplicates: int


def _columns(event: Event) -> tuple:
    key, moment, body = to_event_row(event)
    return (
        key,
        moment,
        event.type,
        event.source,
        event.tenant,
        event.jobid,
        event.taskid,
        event.attempt,
        body.decode(),
    )


def insert_events(conn: Any, events: Iterable[Event]) -> BatchResult:
    """Inserts the events and counts the new rows and the ones the table already held.

    Every event is converted first, so an event the envelope refuses leaves the table untouched.
    A key is the event id with the time of the fact, so the same id with another time is a new row.
    The caller commits, and so composes the insert with the change that produced the events.
    """
    rows = [_columns(event) for event in events]
    inserted = 0
    for start in range(0, len(rows), BATCH_SIZE):
        arrays = [list(column) for column in zip(*rows[start : start + BATCH_SIZE], strict=True)]
        inserted += conn.execute(INSERT, arrays).rowcount
    return BatchResult(inserted=inserted, duplicates=len(rows) - inserted)


def events_for_job(conn: Any, job_id: str) -> list[EventRow]:
    """The rows of one job in the order the facts happened, with the stored body untouched."""
    rows = conn.execute(SELECT_JOB, (job_id,)).fetchall()
    return [EventRow(**dict(zip(EventRow.model_fields, row, strict=True))) for row in rows]
