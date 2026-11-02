"""Behaviour of the hypertable as data ages, with the policy jobs run directly, never awaited."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from wdf_events import build_event
from wdf_eventstore import BatchResult, chunk_count, events_for_job, insert_events
from wdf_eventstore.store import SELECT_JOB

pytestmark = pytest.mark.integration

RETENTION = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "contracts/state/retention.yaml").read_text()
)
JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"


def started(time, sequence=0, job=JOB):
    return build_event(
        "job.started",
        source="/wdf/workflow",
        tenant="acme",
        jobid=job,
        priorityclass="standard",
        jobclass="short",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=time,
        data={},
        sequence=sequence,
    )


def days_ago(days: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def run_policy(db, proc_name: str) -> None:
    (job_id,) = db.execute(
        "SELECT job_id FROM timescaledb_information.jobs"
        " WHERE hypertable_name = 'event' AND proc_name = %s",
        (proc_name,),
    ).fetchone()
    db.execute("CALL run_job(%s)", (job_id,))


def compressed_chunks(db) -> int:
    return db.execute(
        "SELECT count(*) FROM timescaledb_information.chunks"
        " WHERE hypertable_name = 'event' AND is_compressed"
    ).fetchone()[0]


def rows(db) -> int:
    return db.execute("SELECT count(*) FROM event").fetchone()[0]


def test_duplicate_into_a_compressed_chunk_inserts_nothing(db):
    old = started(days_ago(5))
    assert insert_events(db, [old]) == BatchResult(1, 0)
    run_policy(db, "policy_compression")
    assert compressed_chunks(db) == 1
    assert insert_events(db, [old]) == BatchResult(inserted=0, duplicates=1)
    assert insert_events(db, [old, started(days_ago(5), sequence=1)]) == BatchResult(1, 1)
    assert rows(db) == 2


def test_events_for_job_reads_compressed_chunks_byte_exact(db):
    events = [started(days_ago(5), sequence=n) for n in range(3)]
    insert_events(db, events)
    before = events_for_job(db, JOB)
    run_policy(db, "policy_compression")
    assert compressed_chunks(db) == 1
    assert events_for_job(db, JOB) == before


def test_retention_drops_whole_chunks_older_than_the_window(db):
    drop_days = RETENTION["windows"]["event"]["drop_after_days"]
    edge = (datetime.now(UTC) - timedelta(days=drop_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # The first chunk ended before the window, the second still reaches into it.
    gone = started(edge - timedelta(hours=12), sequence=1)
    kept_though_older = started(edge + timedelta(hours=1), sequence=2)
    recent = started(days_ago(1), sequence=3)
    insert_events(db, [gone, kept_though_older, recent])
    assert chunk_count(db) == 3
    run_policy(db, "policy_retention")
    assert chunk_count(db) == 2
    assert {row.event_id for row in events_for_job(db, JOB)} == {
        f"{e.source}#{e.id}" for e in (kept_though_older, recent)
    }


def test_chunk_count_follows_compression_and_drop(db):
    assert chunk_count(db) == 0
    drop_days = RETENTION["windows"]["event"]["drop_after_days"]
    insert_events(
        db,
        [
            started(days_ago(drop_days + 5), sequence=1),
            started(days_ago(5), sequence=2),
            started(days_ago(1), sequence=3),
        ],
    )
    assert (chunk_count(db), compressed_chunks(db)) == (3, 0)
    run_policy(db, "policy_compression")
    assert (chunk_count(db), compressed_chunks(db)) == (3, 2)
    run_policy(db, "policy_retention")
    assert (chunk_count(db), compressed_chunks(db)) == (2, 1)


def test_events_for_job_uses_the_job_index(db):
    for day in range(4):
        insert_events(
            db,
            [
                started(days_ago(day + 0.5) + timedelta(seconds=n), sequence=n, job=job)
                for n in range(60)
                for job in (f"00000000-0000-4000-8000-{j:012d}" for j in range(200))
            ],
        )
    db.execute("ANALYZE event")
    (plan,) = db.execute(
        "EXPLAIN (FORMAT JSON) " + SELECT_JOB, ("00000000-0000-4000-8000-000000000007",)
    ).fetchone()[0:1]
    nodes = []

    def walk(node):
        nodes.append(node)
        for child in node.get("Plans", []):
            walk(child)

    walk(plan[0]["Plan"] if isinstance(plan, list) else plan["Plan"])
    assert chunk_count(db) >= 4
    assert {n["Node Type"] for n in nodes if "Seq Scan" in n["Node Type"]} == set()
    assert any(n.get("Index Name", "").endswith("event_job_time") for n in nodes), json.dumps(plan)[
        :600
    ]
