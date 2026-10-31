"""The event row against a real Postgres: dedup on the key pair and the body column type."""

import subprocess
import time
from datetime import UTC, datetime

import psycopg
import pytest
from wdf_events import build_event, to_event_row
from wdf_itest.session import RUN_LABEL

pytestmark = pytest.mark.integration

JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
PASSWORD = "wdf"


def connect(port: int, timeout: float = 90) -> psycopg.Connection:
    """The published port accepts connections before the server is up, so retry until it answers."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return psycopg.connect(
                host="127.0.0.1", port=port, user="postgres", password=PASSWORD, autocommit=True
            )
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def labelled(run_id: str) -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={RUN_LABEL}={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def test_a_replayed_event_adds_no_row_and_the_time_survives_to_the_microsecond(t1):
    event = build_event(
        "job.started",
        source="/wdf/workflow",
        tenant="acme",
        jobid=JOB,
        priorityclass="standard",
        jobclass="simulate",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 10, 30, 12, 0, 0, 123456, tzinfo=UTC),
        data={},
    )
    key, moment, body = to_event_row(event)
    assert event.time.endswith(".123456Z")
    unsorted_body = '{"b":1,"a":{"d":1,"c":2}}'
    with t1.container("postgres", ports={0: 5432}, env={"POSTGRES_PASSWORD": PASSWORD}) as c:
        with connect(c.host_ports[0]) as db:
            db.execute(
                "CREATE TABLE event (event_id text NOT NULL, event_time timestamptz NOT NULL,"
                " body text NOT NULL, UNIQUE (event_id, event_time))"
            )
            insert = (
                "INSERT INTO event VALUES (%s, %s, %s)"
                " ON CONFLICT (event_id, event_time) DO NOTHING"
            )
            assert db.execute(insert, (key, moment, body.decode())).rowcount == 1
            assert db.execute(insert, (key, moment, body.decode())).rowcount == 0
            stored, stored_body = db.execute("SELECT event_time, body FROM event").fetchone()
            assert stored == moment
            assert stored.microsecond == 123456
            assert stored_body.encode() == body

            db.execute("CREATE TABLE as_text (body text)")
            db.execute("CREATE TABLE as_jsonb (body jsonb)")
            db.execute("INSERT INTO as_text VALUES (%s)", (unsorted_body,))
            db.execute("INSERT INTO as_jsonb VALUES (%s)", (unsorted_body,))
            assert db.execute("SELECT body FROM as_text").fetchone()[0] == unsorted_body
            assert db.execute("SELECT body::text FROM as_jsonb").fetchone()[0] != unsorted_body
        assert labelled(t1.run_id) == [c.id[:12]]
    assert labelled(t1.run_id) == []
