"""The invariant queries against a real database that starts out consistent."""

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from wdf_artifactindex import index_reference, link_use, mark_deleted
from wdf_events import build_event
from wdf_eventstore import events_for_job, insert_events
from wdf_jobstate import JobRow, get_job, insert_accepted, mark_published, transition
from wdf_keys import workflow_id
from wdf_stateops import check_invariants

pytestmark = pytest.mark.integration

SHA = "a" * 64
TRACE = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
TIME = datetime(2026, 11, 1, 12, 0, 0, 123456, tzinfo=UTC)


def make_row(**overrides) -> JobRow:
    fields = {
        "job_id": str(uuid.uuid4()),
        "tenant": "acme",
        "submission_key": f"key-{uuid.uuid4()}",
        "batch_id": str(uuid.uuid4()),
        "priority_class": "standard",
        "manifest_ref": {
            "uri": f"s3://wdf-sources/sha256/aa/{SHA}",
            "sha256": SHA,
            "size": 7,
            "name": "m",
            "kind": "manifest",
        },
        "schema_version": "urn:wdf:schema:job.accepted:1.0",
        "traceparent": TRACE,
        "seed": 3,
        "parameters": {"a": 1},
        "fingerprint": "b" * 64,
    }
    return JobRow(**{**fields, **overrides})


def make_event(name, row: JobRow, data=None, jobclass=None, **attempt):
    return build_event(
        name,
        source="/wdf/dispatch",
        tenant=row.tenant,
        jobid=row.job_id,
        priorityclass=row.priority_class,
        jobclass=jobclass or row.job_class,
        traceparent=TRACE,
        time=TIME,
        data=data or {},
        **attempt,
    )


def accepted_event(row: JobRow, jobclass=None):
    data = {
        "manifest": row.manifest_ref,
        "batch_id": row.batch_id,
        "submission_key": row.submission_key,
    }
    return make_event("job.accepted", row, data, jobclass)


def result_reference(row: JobRow) -> dict:
    return {
        "uri": f"s3://wdf-results/acme/b/{row.job_id}/seed-7/sim/r0a1/result.xml",
        "sha256": "4" * 64,
        "size": 1,
        "name": "result.xml",
        "kind": "result",
        "producer": {"job": row.job_id, "task": "sim", "attempt": "r0a1"},
    }


def result_ready_event(row: JobRow, reference: dict):
    return make_event("task.result.ready", row, {"result": reference}, taskid="sim", attempt=1)


def publish(db, row: JobRow) -> None:
    """A job as the submit sequence leaves it: row, events, then the receipt."""
    insert_accepted(db, [row])
    insert_events(db, [accepted_event(row), make_event("job.published", row)])
    mark_published(db, row.job_id)


def run(db, row: JobRow) -> None:
    transition(db, row.job_id, "accepted", "admitted")
    transition(db, row.job_id, "admitted", "running")


def consistent_world(db) -> JobRow:
    """A running job with a workflow, a receipt, matching classes and an indexed result."""
    row = make_row()
    publish(db, row)
    run(db, row)
    reference = result_reference(row)
    index_reference(db, reference)
    insert_events(db, [result_ready_event(row, reference)])
    return row


def open_workflows(*rows: JobRow) -> list[str]:
    return [workflow_id(row.job_id) for row in rows]


def lines(report) -> list[str]:
    return [f"invariant: {name}: {key}" for name, key in report.violations]


def test_clean_database_reports_nothing(db):
    row = consistent_world(db)
    report = check_invariants(db, open_workflows(row))
    assert report.violations == [] and report.skipped == []


def test_running_job_without_an_open_workflow_is_reported(db):
    row = consistent_world(db)
    report = check_invariants(db, [])
    assert lines(report) == [f"invariant: running-without-workflow: {row.job_id}"]


def test_open_workflow_of_a_terminal_row_is_reported(db):
    row = consistent_world(db)
    transition(db, row.job_id, "running", "succeeded")
    report = check_invariants(db, open_workflows(row))
    assert lines(report) == [
        f"invariant: open-workflow-without-live-row: {workflow_id(row.job_id)}"
    ]


def test_open_workflow_without_a_row_is_reported(db):
    ghost = workflow_id(str(uuid.uuid4()))
    report = check_invariants(db, [ghost])
    assert lines(report) == [f"invariant: open-workflow-without-live-row: {ghost}"]


def test_accepted_and_dead_lettered_jobs_are_never_reported(db):
    accepted, dead = make_row(), make_row()
    publish(db, accepted)
    insert_accepted(db, [dead])
    insert_events(
        db,
        [
            accepted_event(dead),
            make_event("job.dead.lettered", dead, {"dlq_reason": "MALFORMED", "detail": "x"}),
        ],
    )
    assert check_invariants(db, []).violations == []


def test_without_a_workflow_list_the_two_workflow_checks_are_skipped(db):
    consistent_world(db)
    report = check_invariants(db)
    assert report.violations == []
    assert report.skipped == ["open-workflow-without-live-row", "running-without-workflow"]


def test_published_event_without_a_receipt_is_reported(db):
    row = make_row()
    insert_accepted(db, [row])
    insert_events(db, [accepted_event(row), make_event("job.published", row)])
    report = check_invariants(db, [])
    assert lines(report) == [f"invariant: published-without-receipt: {row.job_id}"]


def test_job_past_accepted_without_a_receipt_is_reported(db):
    row = make_row()
    insert_accepted(db, [row])
    transition(db, row.job_id, "accepted", "cancelled", row_only=True)
    report = check_invariants(db, [])
    assert lines(report) == [f"invariant: published-without-receipt: {row.job_id}"]


def test_result_ready_event_without_an_artifact_row_is_reported(db):
    row = make_row()
    publish(db, row)
    run(db, row)
    event = result_ready_event(row, result_reference(row))
    insert_events(db, [event])
    report = check_invariants(db, open_workflows(row))
    assert lines(report) == [f"invariant: result-ready-without-artifact: {event.source}#{event.id}"]


def test_accepted_event_class_differing_from_the_row_is_reported(db):
    row = make_row()
    insert_accepted(db, [row])
    event = accepted_event(row, jobclass="long")
    insert_events(db, [event, make_event("job.published", row)])
    mark_published(db, row.job_id)
    report = check_invariants(db, [])
    assert lines(report) == [
        f"invariant: accepted-class-differs-from-row: {event.source}#{event.id}"
    ]


def blob(n: int) -> dict:
    digest = f"{n:064x}"
    return {
        "uri": f"s3://wdf-sources/sha256/{digest[:2]}/{digest}",
        "sha256": digest,
        "size": 1,
        "name": "b",
        "kind": "source",
    }


def deleted(db, ref: dict) -> None:
    mark_deleted(db, ref["uri"], datetime.now(UTC))


def test_retention_invariant_reports_a_seeded_violation(db):
    now = datetime.now(UTC)
    live, finished, ghost, stale = make_row(), make_row(), make_row(), make_row()
    for row in (live, finished):
        publish(db, row)
    run(db, live)
    run(db, finished)
    transition(db, finished.job_id, "running", "succeeded")

    held, released, fresh, old = blob(1), blob(2), blob(3), blob(4)
    for ref, job, at in (
        (held, live.job_id, now),
        (released, finished.job_id, now),
        (fresh, ghost.job_id, now),
        (old, stale.job_id, now - timedelta(days=3)),
    ):
        index_reference(db, ref)
        link_use(db, job, ref["uri"], at)
        deleted(db, ref)

    output = result_reference(live)
    index_reference(db, output)
    mark_deleted(db, output["uri"], now)
    finished_output = result_reference(finished)
    index_reference(db, finished_output)
    mark_deleted(db, finished_output["uri"], now)
    checkpoint = {
        **output,
        "uri": f"s3://wdf-checkpoints/acme/b/{live.job_id}/seed-7/sim/r0a1/s1/checkpoint.json",
        "kind": "checkpoint",
        "name": "checkpoint.json",
    }
    index_reference(db, checkpoint)
    mark_deleted(db, checkpoint["uri"], now)

    report = check_invariants(db)
    reported = sorted(
        key for name, key in report.violations if name == "deleted-artifact-of-live-job"
    )
    assert reported == sorted([held["uri"], fresh["uri"], output["uri"]])


def test_row_and_event_commit_or_roll_back_together(appdb):
    row = make_row()
    with psycopg.connect(appdb) as writer, psycopg.connect(appdb, autocommit=True) as reader:
        with pytest.raises(RuntimeError), writer.transaction():
            insert_accepted(writer, [row])
            insert_events(writer, [accepted_event(row)])
            raise RuntimeError("the caller failed after both writes")
        assert get_job(reader, row.job_id) is None
        assert events_for_job(reader, row.job_id) == []

        with writer.transaction():
            insert_accepted(writer, [row])
            insert_events(writer, [accepted_event(row)])
            assert get_job(reader, row.job_id) is None
            assert events_for_job(reader, row.job_id) == []
        assert get_job(reader, row.job_id).job_id == row.job_id
        assert len(events_for_job(reader, row.job_id)) == 1


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wdf_stateops.invariants", *args],
        capture_output=True,
        text=True,
    )


def test_cli_exits_0_clean_1_violated_and_2_without_a_dsn(appdb, db, tmp_path):
    workflows = tmp_path / "open.txt"
    row = consistent_world(db)
    workflows.write_text(f"\n{workflow_id(row.job_id)}\n\n")
    clean = cli("--dsn", appdb, "--open-workflows", str(workflows))
    assert (clean.returncode, clean.stdout) == (0, "")

    skipping = cli("--dsn", appdb)
    assert skipping.returncode == 0
    assert skipping.stdout.splitlines() == [
        "skipped: open-workflow-without-live-row",
        "skipped: running-without-workflow",
    ]

    workflows.write_text("")
    violated = cli("--dsn", appdb, "--open-workflows", str(workflows))
    assert violated.returncode == 1
    assert violated.stdout == f"invariant: running-without-workflow: {row.job_id}\n"

    assert cli().returncode == 2


def test_cli_exits_3_when_the_database_is_unreachable():
    result = cli("--dsn", "postgresql://postgres@127.0.0.1:1/none?connect_timeout=2")
    assert result.returncode == 3
