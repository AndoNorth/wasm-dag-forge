"""The id of a built event derives from its facts, and the time comes from the fact."""

import json
from datetime import UTC, datetime

import pytest
from wdf_events import build_event, to_bytes
from wdf_keys import attempt_identity, cloudevent_id

JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
MOMENT = datetime(2026, 10, 30, 12, 0, 0, 123456, tzinfo=UTC)
CHECKPOINT = {
    "uri": "s3://wdf-checkpoints/acme/b/j/7/sim/r0a1/0/state.bin",
    "sha256": "9" * 64,
    "size": 1,
    "name": "state.bin",
    "kind": "checkpoint",
    "producer": {"job": JOB, "task": "sim", "attempt": "r0a1"},
}


def build(name="task.started", **over):
    facts = dict(
        source="/wdf/workflow",
        tenant="acme",
        jobid=JOB,
        priorityclass="standard",
        jobclass="simulate",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=MOMENT,
        data={},
        taskid="sim",
        attempt=1,
    )
    if name.startswith("job."):
        facts.update(taskid=None, attempt=None)
    return build_event(name, **{**facts, **over})


def test_the_id_is_the_key_formula_over_the_facts():
    event = build()
    assert event.id == cloudevent_id(JOB, "sim", attempt_identity(1, 0), "task.started", 0)
    assert build("job.started").id == cloudevent_id(JOB, "", "", "job.started", 0)


def test_a_rebuild_is_equal_and_gives_equal_bytes():
    assert build() == build()
    assert to_bytes(build()) == to_bytes(build())


def test_the_time_has_six_fractional_digits_and_never_enters_the_id():
    event = build()
    assert event.time == "2026-10-30T12:00:00.123456Z"
    later = build(time=datetime(2026, 11, 1, tzinfo=UTC))
    assert later.time == "2026-11-01T00:00:00.000000Z"
    assert later.id == event.id


@pytest.mark.parametrize("change", [{"attempt": 2}, {"resume_counter": 1}, {"taskid": "agg"}])
def test_a_different_attempt_resume_or_task_gives_a_different_id(change):
    assert build(**change).id != build().id


def test_an_attempt_restart_after_a_resume_does_not_collide():
    assert build(attempt=1, resume_counter=1).id != build(attempt=1, resume_counter=0).id


def test_the_checkpoint_sequence_feeds_the_id():
    first = build("task.checkpoint.committed", data={"sequence": 0, "checkpoint": CHECKPOINT})
    second = build("task.checkpoint.committed", data={"sequence": 1, "checkpoint": CHECKPOINT})
    assert first.id != second.id
    assert second.id == cloudevent_id(JOB, "sim", "r0a1", "task.checkpoint.committed", 1)


def test_different_types_of_one_job_never_share_an_id():
    assert build("job.started").id != build("job.paused").id


def test_the_data_and_the_context_are_checked_when_serialising():
    with pytest.raises(Exception, match="unexpected"):
        to_bytes(build(data={"unexpected": 1}))
    assert json.loads(to_bytes(build()))["subject"] == f"{JOB}/sim"


@pytest.mark.parametrize(
    "name, over",
    [
        ("task.started", {"taskid": None}),
        ("task.started", {"attempt": None}),
        ("job.started", {"taskid": "sim"}),
        ("job.started", {"attempt": 1}),
        ("task.placed", {"attempt": 1}),
        ("job.nosuch", {}),
    ],
)
def test_facts_that_do_not_fit_the_type_are_refused(name, over):
    with pytest.raises(ValueError):
        build(name, **over)
