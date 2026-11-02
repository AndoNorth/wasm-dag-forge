"""insert_events and events_for_job against a real TimescaleDB."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from wdf_events import build_event, format_time, from_bytes, to_bytes, to_event_row
from wdf_eventstore import BatchResult, events_for_job, insert_events

pytestmark = pytest.mark.integration

GOLDEN = sorted(
    (Path(__file__).resolve().parents[3] / "contracts/events/vectors/golden").glob("*/*.json")
)
JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
TIME = datetime(2026, 10, 30, 12, 0, 0, 123456, tzinfo=UTC)


def started(sequence=0, time=TIME):
    return build_event(
        "job.started",
        source="/wdf/workflow",
        tenant="acme",
        jobid=JOB,
        priorityclass="standard",
        jobclass="short",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=time,
        data={},
        sequence=sequence,
    )


def count(db) -> int:
    return db.execute("SELECT count(*) FROM event").fetchone()[0]


def test_replay_inserts_no_row(db):
    events = [from_bytes(path.read_bytes()) for path in GOLDEN]
    assert len(events) == 26
    assert insert_events(db, events) == BatchResult(inserted=26, duplicates=0)
    assert insert_events(db, events) == BatchResult(inserted=0, duplicates=len(events))
    assert count(db) == len(events)


def test_golden_events_pass_through_unchanged(db):
    insert_events(db, [from_bytes(path.read_bytes()) for path in GOLDEN])
    stored = {row[0] for row in db.execute("SELECT body FROM event").fetchall()}
    assert stored == {path.read_text() for path in GOLDEN}


def test_time_from_the_fact_not_the_write_clock(db):
    event = started()
    assert insert_events(db, [event]) == BatchResult(1, 0)
    assert insert_events(db, [event]) == BatchResult(0, 1)
    written_late = event.model_copy(update={"time": format_time(datetime.now(UTC))})
    assert insert_events(db, [written_late]) == BatchResult(1, 0)
    assert insert_events(db, [event]) == BatchResult(0, 1)
    assert count(db) == 2


def test_time_survives_to_the_microsecond(db):
    insert_events(db, [started()])
    (row,) = events_for_job(db, JOB)
    assert row.event_time == TIME
    assert row.event_time.microsecond == 123456


def test_body_is_byte_exact(db):
    event = started()
    insert_events(db, [event])
    (row,) = events_for_job(db, JOB)
    key, _, body = to_event_row(event)
    assert row.event_id == key
    assert row.body.encode() == body == to_bytes(event)
    assert from_bytes(row.body.encode()) == event


def test_batch_of_ten_thousand_in_one_call(db):
    events = [started(sequence=n) for n in range(10_000)]
    assert insert_events(db, events) == BatchResult(inserted=10_000, duplicates=0)
    assert count(db) == 10_000
    assert insert_events(db, events) == BatchResult(inserted=0, duplicates=10_000)


def test_mixed_new_and_duplicate_counts(db):
    a, b, c, d = (started(sequence=n) for n in range(4))
    assert insert_events(db, [a, b]) == BatchResult(2, 0)
    assert insert_events(db, [b, c, d]) == BatchResult(inserted=2, duplicates=1)
    assert insert_events(db, [a, a]) == BatchResult(inserted=0, duplicates=2)
    assert insert_events(db, []) == BatchResult(0, 0)
    assert count(db) == 4


def test_events_for_job_orders_by_time_and_holds_the_attempt_as_given(db):
    later = build_event(
        "task.started",
        source="/wdf/workflow",
        tenant="acme",
        jobid=JOB,
        priorityclass="standard",
        jobclass="short",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 10, 30, 12, 5, tzinfo=UTC),
        data={},
        taskid="compile",
        attempt=3,
    )
    insert_events(db, [later, started()])
    first, second = events_for_job(db, JOB)
    assert (first.task_id, first.attempt) == (None, None)
    assert (second.task_id, second.attempt) == ("compile", 3)
    assert events_for_job(db, "00000000-0000-0000-0000-000000000000") == []


def test_a_caller_rollback_leaves_no_row(db):
    db.autocommit = False
    insert_events(db, [started()])
    db.rollback()
    db.autocommit = True
    assert count(db) == 0
