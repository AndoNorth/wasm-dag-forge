"""The event history row: key pair and verbatim body."""

from datetime import UTC, datetime

from wdf_events import build_event, from_bytes, to_event_row

JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"


def build(name="job.started", data=None):
    return build_event(
        name,
        source="/wdf/workflow",
        tenant="acme",
        jobid=JOB,
        priorityclass="standard",
        jobclass="simulate",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 10, 30, 12, 0, 0, 123456, tzinfo=UTC),
        data=data or {},
    )


def test_the_row_is_the_key_pair_the_time_and_the_canonical_bytes():
    event = build()
    key, moment, body = to_event_row(event)
    assert key == f"{event.source}#{event.id}"
    assert moment == datetime(2026, 10, 30, 12, 0, 0, 123456, tzinfo=UTC)
    assert from_bytes(body) == event


def test_two_builds_of_one_event_give_equal_rows():
    assert to_event_row(build()) == to_event_row(build())


def test_the_id_half_of_the_key_never_holds_a_hash():
    for event in (build(), build("job.paused"), build("job.partial", {"skipped_tasks": ["a"]})):
        assert to_event_row(event)[0].count("#") == 1
