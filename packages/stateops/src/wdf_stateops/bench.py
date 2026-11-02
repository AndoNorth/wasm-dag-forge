"""`python -m wdf_stateops.bench --jobs N [--dsn DSN] [--twice]`.

Inserts N accepted rows in chunks of 1,000, each chunk in one transaction with its accepted events,
then the remaining 13 events of a two-task job, through the same libraries the writers will call.
Every key and time derives from the job number, so a second run inserts nothing.
It asserts counts and idempotence and has no timing guard, the seconds it prints are the measure.
"""

import argparse
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import psycopg
from wdf_events import Event, build_event
from wdf_events.models import ATTEMPT_TYPES
from wdf_eventstore import chunk_count, insert_events
from wdf_jobstate import JobRow, insert_accepted
from wdf_keys import batch_id, job_id, workflow_id

CHUNK = 1_000
TENANT = "bench"
TASKS = ("compile", "sim")
EVENTS_PER_JOB = 14
# A fixed origin keeps every event key, which includes the time, the same from run to run.
ORIGIN = datetime(2026, 11, 1, tzinfo=UTC)
JOB_SPACING = timedelta(seconds=6)
EVENT_SPACING = timedelta(milliseconds=400)
OK, WRONG_COUNT = 0, 1

JOB_EVENTS = (
    "job.accepted",
    "job.published",
    "job.admitted",
    "job.started",
    "job.succeeded",
    "job.paused",
)
TASK_EVENTS = ("task.placed", "task.started", "task.result.ready", "task.succeeded")
SOURCES = {
    "job.accepted": "/wdf/dispatch",
    "job.published": "/wdf/dispatch",
    "job.admitted": "/wdf/admission",
}
WORKFLOW = "/wdf/workflow"

# The rows and events of the bench tenant that the N jobs own, as the database sees them.
BENCH_KEYS = "SELECT 'bench-' || g FROM generate_series(0, %(last)s) AS g"
COUNT_ROWS = (
    "SELECT count(*) FROM accepted_job"
    f" WHERE tenant = %(tenant)s AND submission_key IN ({BENCH_KEYS})"
)
COUNT_EVENTS = (
    "SELECT count(*) FROM event WHERE tenant = %(tenant)s AND job_id IN"
    " (SELECT job_id FROM accepted_job WHERE tenant = %(tenant)s"
    f" AND submission_key IN ({BENCH_KEYS}))"
)
SIZES = "SELECT pg_total_relation_size('accepted_job'), hypertable_size('event')"


@dataclass(frozen=True)
class Run:
    rows_inserted: int
    rows_duplicates: int
    events_inserted: int
    events_duplicates: int
    rows_seconds: float
    events_seconds: float


def job_row(i: int) -> JobRow:
    key = f"bench-{i}"
    job = job_id(TENANT, key)
    digest = sha256(key.encode()).hexdigest()
    return JobRow(
        job_id=job,
        tenant=TENANT,
        submission_key=key,
        batch_id=batch_id(TENANT, f"batch-{i // CHUNK}"),
        priority_class="standard",
        manifest_ref={
            "uri": f"s3://wdf-sources/sha256/{digest[:2]}/{digest}",
            "sha256": digest,
            "size": 2048,
            "name": "manifest.json",
            "kind": "manifest",
        },
        schema_version="urn:wdf:schema:job.accepted:1.0",
        traceparent=f"00-{job.replace('-', '')}-{i + 1:016x}-01",
        seed=i,
        parameters={"class_override": "short"},
        fingerprint=digest,
    )


def job_events(i: int, row: JobRow) -> list[Event]:
    """The 14 events of a two-task job in the order they happened, the accepted event first."""
    plan = [(name, None) for name in JOB_EVENTS] + [
        (name, task) for task in TASKS for name in TASK_EVENTS
    ]
    events = []
    for sequence, (name, task) in enumerate(plan):
        data = {}
        if name == "job.accepted":
            data = {
                "manifest": row.manifest_ref,
                "batch_id": row.batch_id,
                "submission_key": row.submission_key,
            }
        elif name == "job.admitted":
            data = {"workflow_id": workflow_id(row.job_id)}
        elif name == "task.placed":
            data = {"queue": "pool-small"}
        elif name == "task.result.ready":
            prefix = f"s3://wdf-results/{TENANT}/{row.batch_id}/{row.job_id}/{i}/{task}/r1a1"
            producer = {"job": row.job_id, "task": task, "attempt": "r1a1"}
            data = {
                "result": {
                    "uri": f"{prefix}/result.bin",
                    "sha256": row.fingerprint,
                    "size": 8192,
                    "name": "result.bin",
                    "kind": "result",
                    "producer": producer,
                }
            }
        events.append(
            build_event(
                name,
                source=SOURCES.get(name, WORKFLOW),
                tenant=TENANT,
                jobid=row.job_id,
                priorityclass=row.priority_class,
                jobclass=row.job_class,
                traceparent=row.traceparent,
                time=ORIGIN + JOB_SPACING * i + EVENT_SPACING * sequence,
                data=data,
                taskid=task,
                attempt=1 if name in ATTEMPT_TYPES else None,
                sequence=sequence,
            )
        )
    return events


def chunks(jobs: int):
    for start in range(0, jobs, CHUNK):
        yield range(start, min(start + CHUNK, jobs))


def insert_run(conn, jobs: int) -> Run:
    """One pass over the rows and one over the remaining events, timing only database work."""
    rows_inserted = rows_duplicates = events_inserted = events_duplicates = 0
    rows_seconds = events_seconds = 0.0
    for numbers in chunks(jobs):
        rows = [job_row(i) for i in numbers]
        accepted = [job_events(i, row)[0] for i, row in zip(numbers, rows, strict=True)]
        started = time.monotonic()
        with conn.transaction():
            result = insert_accepted(conn, rows)
            batch = insert_events(conn, accepted)
        rows_seconds += time.monotonic() - started
        rows_inserted += len(result.inserted)
        rows_duplicates += len(result.duplicates)
        events_inserted += batch.inserted
        events_duplicates += batch.duplicates
    for numbers in chunks(jobs):
        remaining = [e for i in numbers for e in job_events(i, job_row(i))[1:]]
        started = time.monotonic()
        with conn.transaction():
            batch = insert_events(conn, remaining)
        events_seconds += time.monotonic() - started
        events_inserted += batch.inserted
        events_duplicates += batch.duplicates
    return Run(
        rows_inserted,
        rows_duplicates,
        events_inserted,
        events_duplicates,
        rows_seconds,
        events_seconds,
    )


def problems(conn, jobs: int, runs: list[Run]) -> list[str]:
    """Every count that disagrees with what N jobs of 14 events must leave behind."""
    found = []
    for number, run in enumerate(runs, 1):
        rows_seen = run.rows_inserted + run.rows_duplicates
        events_seen = run.events_inserted + run.events_duplicates
        if rows_seen != jobs:
            found.append(f"run {number}: {rows_seen} rows seen, expected {jobs}")
        if events_seen != jobs * EVENTS_PER_JOB:
            found.append(
                f"run {number}: {events_seen} events seen, expected {jobs * EVENTS_PER_JOB}"
            )
    last = runs[-1]
    if len(runs) > 1 and (last.rows_inserted or last.events_inserted):
        found.append(
            f"the repeat inserted {last.rows_inserted} rows and {last.events_inserted} events"
        )
    params = {"tenant": TENANT, "last": jobs - 1}
    stored_rows = conn.execute(COUNT_ROWS, params).fetchone()[0]
    stored_events = conn.execute(COUNT_EVENTS, params).fetchone()[0]
    if stored_rows != jobs:
        found.append(f"{stored_rows} rows stored, expected {jobs}")
    if stored_events != jobs * EVENTS_PER_JOB:
        found.append(f"{stored_events} events stored, expected {jobs * EVENTS_PER_JOB}")
    return found


def run_bench(conn, jobs: int, twice: bool = False) -> tuple[list[Run], list[str]]:
    """Runs the insert once or twice and returns each run with the problems found."""
    runs = [insert_run(conn, jobs) for _ in range(2 if twice else 1)]
    return runs, problems(conn, jobs, runs)


def report(conn, jobs: int, runs: list[Run]) -> None:
    for number, run in enumerate(runs, 1):
        print(f"run: {number}")
        print(f"rows_inserted: {run.rows_inserted}")
        print(f"rows_duplicates: {run.rows_duplicates}")
        print(f"events_inserted: {run.events_inserted}")
        print(f"events_duplicates: {run.events_duplicates}")
        print(f"rows_seconds: {run.rows_seconds:.2f}")
        print(f"events_seconds: {run.events_seconds:.2f}")
        if run.rows_inserted and run.events_inserted > run.rows_inserted:
            # The accepted events travel with their rows, so the rest are the second phase.
            later = run.events_inserted - run.rows_inserted
            print(f"rows_per_second: {run.rows_inserted / run.rows_seconds:.0f}")
            print(f"events_per_second: {later / run.events_seconds:.0f}")
    accepted_bytes, event_bytes = conn.execute(SIZES).fetchone()
    print(f"accepted_job_bytes: {accepted_bytes}")
    print(f"event_bytes: {event_bytes}")
    print(f"insert_chunks: {-(-jobs // CHUNK)}")
    print(f"event_chunks: {chunk_count(conn)}")


@contextmanager
def database(dsn: str | None):
    """The given database, or a fresh migrated one in a container of the T1 helper."""
    if dsn:
        yield dsn
        return
    from wdf_itest import Session
    from wdf_migrate import apply, discover
    from wdf_migrate.fixtures import IMAGE, PASSWORD, TELEMETRY_OFF, _connect

    with (
        Session() as session,
        session.container(
            IMAGE, ports={0: 5432}, command=TELEMETRY_OFF, env={"POSTGRES_PASSWORD": PASSWORD}
        ) as container,
    ):
        port = container.host_ports[0]
        _connect(port).close()
        url = f"postgresql://postgres:{PASSWORD}@127.0.0.1:{port}/postgres"
        apply(url, discover())
        yield url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_stateops.bench")
    parser.add_argument("--jobs", type=int, required=True)
    parser.add_argument("--dsn")
    parser.add_argument("--twice", action="store_true")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    with database(args.dsn) as dsn, psycopg.connect(dsn, autocommit=True) as conn:
        runs, found = run_bench(conn, args.jobs, args.twice)
        report(conn, args.jobs, runs)
    for line in found:
        print(f"wrong: {line}", file=sys.stderr)
    return WRONG_COUNT if found else OK


if __name__ == "__main__":
    sys.exit(main())
