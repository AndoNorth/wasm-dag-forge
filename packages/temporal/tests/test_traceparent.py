"""The traceparent of the submit stage: valid, checked by the event profile, tracestate optional."""

import re
from datetime import UTC, datetime

import pytest
from wdf_events import SchemaInvalid, build_event, to_bytes
from wdf_temporal import new_traceparent, trace_id_of

JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"


def event(traceparent, tracestate=None):
    return build_event(
        "job.started",
        source="/wdf/workflow",
        tenant="acme",
        jobid=JOB,
        priorityclass="standard",
        jobclass="simulate",
        traceparent=traceparent,
        tracestate=tracestate,
        time=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        data={},
    )


def test_a_new_traceparent_is_a_valid_sampled_w3c_value_and_is_accepted():
    value = new_traceparent()
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", value)
    assert trace_id_of(value) == value.split("-")[1]
    assert to_bytes(event(value))


def test_every_call_gives_a_new_trace():
    assert len({trace_id_of(new_traceparent()) for _ in range(50)}) == 50


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-traceparent",
        "00-" + "0" * 32 + "-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01",
        "00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01",
        "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",
    ],
)
def test_a_malformed_traceparent_is_rejected_by_the_profile(bad):
    with pytest.raises(SchemaInvalid):
        to_bytes(event(bad))


def test_tracestate_is_optional():
    value = new_traceparent()
    assert "tracestate" not in to_bytes(event(value)).decode()
    assert '"tracestate":"vendor=1"' in to_bytes(event(value, "vendor=1")).decode()
