"""A batch the envelope refuses never reaches the database."""

from datetime import UTC, datetime

import pytest
from wdf_events import EventTooLarge, build_event
from wdf_eventstore import insert_events


class NoDatabase:
    def execute(self, *args):
        raise AssertionError("the database was used")


def event(data):
    return build_event(
        "job.started",
        source="/wdf/workflow",
        tenant="acme",
        jobid="3a9617fa-df7c-5760-a644-42d559e0f433",
        priorityclass="standard",
        jobclass="short",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 10, 30, 12, tzinfo=UTC),
        data=data,
    )


def test_oversized_event_is_refused_before_insert():
    fits, too_big = event({}), event({"padding": "x" * 65_536})
    with pytest.raises(EventTooLarge):
        insert_events(NoDatabase(), [fits, too_big])
