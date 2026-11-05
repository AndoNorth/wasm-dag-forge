"""The inputs of contracts/state/inputs.yaml against a real database and a stand-in store."""

import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from wdf_artifactindex import index_reference
from wdf_events import build_event
from wdf_eventstore import insert_events
from wdf_jobstate import JobRow, insert_accepted, transition
from wdf_stateops import inputs
from wdf_stateops.inputs import Refused, Store, StoreUnavailable, load, parse

SQL_INPUTS = [i.name for i in load().inputs if i.source.kind == "sql"]
CONTRACT_INPUTS = [i.name for i in load().inputs if i.source.kind in ("sql", "invariants")]
ROOT = Path(__file__).resolve().parents[3]
FAILURE_CODES = sorted(
    c["code"]
    for c in yaml.safe_load((ROOT / "contracts/artifact/failures.yaml").read_text())["codes"]
)
SHA = "a" * 64
TRACE = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def values(db, store=None) -> dict[str, float]:
    return dict(inputs.collect_inputs(db, load(), store).values)


def event_at(time: datetime):
    return build_event(
        "job.started",
        source="/wdf/dispatch",
        tenant="acme",
        jobid=str(uuid.uuid4()),
        priorityclass="standard",
        jobclass="short",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=time,
        data={},
    )


@pytest.mark.integration
def test_every_sql_input_returns_a_number_on_a_fresh_database(db):
    found = values(db)
    for name in SQL_INPUTS:
        assert isinstance(found[name], int | float), name
    assert found["event_chunks"] == 0
    assert found["drift_rows"] == 0
    assert found["connections"] >= 1
    assert found["database_size"] > 0


@pytest.mark.integration
def test_ingest_lag_grows_with_the_age_of_the_newest_event(db):
    now = datetime.now(UTC)
    insert_events(db, [event_at(now - timedelta(hours=1))])
    first = values(db)["event_ingest_lag"]
    assert 3600 <= first < 3700
    time.sleep(1.1)
    assert values(db)["event_ingest_lag"] >= first + 1
    insert_events(db, [event_at(now - timedelta(minutes=10))])
    assert 600 <= values(db)["event_ingest_lag"] < 700


@pytest.mark.integration
def test_the_command_prints_a_line_per_input_and_skips_the_store_inputs(appdb):
    run = subprocess.run(
        [sys.executable, "-m", "wdf_stateops.inputs", "--dsn", appdb],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    printed = [line.split("{")[0].split()[0] for line in lines if not line.startswith("skipped")]
    assert [n for n in printed if not n.startswith("artifact_failures")] == CONTRACT_INPUTS
    assert [n for n in printed if n.startswith("artifact_failures")] == ["artifact_failures"] * len(
        FAILURE_CODES
    )
    assert lines[:2] == ["skipped: store_bytes", "skipped: store_capacity_share"]


def test_an_unreachable_database_exits_three():
    run = subprocess.run(
        [sys.executable, "-m", "wdf_stateops.inputs", "--dsn", "postgresql://127.0.0.1:1/x"],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 3


def test_a_store_endpoint_needs_credentials():
    run = subprocess.run(
        [sys.executable, "-m", "wdf_stateops.inputs", "--dsn", "x", "--store-endpoint", "u"],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 2


def fake_df(output: str, code: int = 0):
    return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=output, stderr="no pod")


DF = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sdd 1000 250 750 25% /data\n"


def test_the_capacity_share_is_the_used_share_of_the_volume(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_df(DF))
    assert inputs.capacity_share(Store("u", "f")) == 25


def test_a_failed_df_is_a_store_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_df("", 1))
    with pytest.raises(StoreUnavailable, match="no pod"):
        inputs.capacity_share(Store("u", "f"))


def test_bytes_per_bucket_come_from_the_capacity_mode(monkeypatch, tmp_path):
    def capacity(endpoint, credentials, mode, say):
        assert mode == "capacity"
        say("bytes wdf-sources 7")
        say("bytes wdf-results 0")
        return 0

    credentials = tmp_path / "c.json"
    credentials.write_text('{"reaper": {"access_key": "a", "secret_key": "b"}}')
    monkeypatch.setattr(inputs, "run_storecheck", capacity)
    assert inputs.store_bytes(Store("u", credentials)) == {"wdf-sources": 7, "wdf-results": 0}


def test_a_failing_capacity_mode_is_a_store_error(monkeypatch, tmp_path):
    credentials = tmp_path / "c.json"
    credentials.write_text('{"admin": {"access_key": "a", "secret_key": "b"}}')

    def capacity(endpoint, credentials, mode, say):
        say("FAIL capacity: the credentials file has no reaper identity")
        return 1

    monkeypatch.setattr(inputs, "run_storecheck", capacity)
    with pytest.raises(StoreUnavailable, match="reaper"):
        inputs.store_bytes(Store("u", credentials))


def task_event(name: str, job: str, task: str, data: dict, attempt: int | None = None, **kwargs):
    return build_event(
        name,
        source="/wdf/workflow",
        tenant="acme",
        jobid=job,
        priorityclass="standard",
        jobclass="short",
        traceparent=TRACE,
        time=datetime.now(UTC),
        data=data,
        taskid=task,
        attempt=attempt,
        **kwargs,
    )


def failed(job: str, reason: str, attempt: int):
    return task_event("task.attempt.failed", job, "sim", {"reason": reason}, attempt)


@pytest.mark.integration
def test_artifact_failures_returns_one_value_per_code(db):
    found = values(db)
    assert {k for k in found if k.startswith("artifact_failures")} == {
        f"artifact_failures{{code={code}}}" for code in FAILURE_CODES
    }
    assert {v for k, v in found.items() if k.startswith("artifact_failures")} == {0}

    job = str(uuid.uuid4())
    insert_events(
        db,
        [
            failed(job, "ARTIFACT_LOST: the object is gone", 1),
            failed(job, "ARTIFACT_LOST: again", 2),
            failed(job, "STORE_UNAVAILABLE: timeout", 3),
            failed(job, "the guest trapped", 4),
            failed(job, "NOT_A_CODE: but shaped like one", 5),
        ],
    )
    found = values(db)
    assert found["artifact_failures{code=ARTIFACT_LOST}"] == 2
    assert found["artifact_failures{code=STORE_UNAVAILABLE}"] == 1
    counted = {k: v for k, v in found.items() if k.startswith("artifact_failures") and v}
    assert len(counted) == 2


@pytest.mark.integration
def test_cache_hits_count_skipped_events_and_misses_the_compile_tasks_that_started(db):
    job = str(uuid.uuid4())
    request = {
        "task_type": "compile",
        "cpu_cores": 1,
        "memory_mib": 64,
        "inputs": [],
        "deadline": "2026-11-05T12:00:00.000000Z",
    }
    insert_events(
        db,
        [
            task_event(
                "task.skipped", job, "build-a", {"reason": "compile-cache-hit", "cache_key": SHA}
            ),
            task_event(
                "task.skipped", job, "build-b", {"reason": "compile-cache-hit", "cache_key": SHA}
            ),
            task_event("task.requested", job, "build-c", request),
            task_event("task.started", job, "build-c", {}, 1),
            task_event("task.started", job, "build-c", {}, 2, sequence=1),
            task_event("task.requested", job, "sim", {**request, "task_type": "simulate"}),
            task_event("task.started", job, "sim", {}, 1),
        ],
    )
    found = values(db)
    assert (found["cache_hits"], found["cache_misses"]) == (2, 1)


def job_row() -> JobRow:
    return JobRow(
        job_id=str(uuid.uuid4()),
        tenant="acme",
        submission_key=f"key-{uuid.uuid4()}",
        batch_id=str(uuid.uuid4()),
        priority_class="standard",
        manifest_ref={
            "uri": f"s3://wdf-sources/sha256/aa/{SHA}",
            "sha256": SHA,
            "size": 7,
            "name": "m",
            "kind": "manifest",
        },
        schema_version="wdf.job.accepted/3",
        traceparent=TRACE,
        seed=3,
        parameters={},
        fingerprint="b" * 64,
    )


def finished_job(db, terminal_ago: timedelta, bucket: str = "wdf-results", kind: str = "result"):
    """A succeeded job that ended the given time ago, with one live attempt-scoped row."""
    row = job_row()
    insert_accepted(db, [row])
    for before, after in (
        ("accepted", "admitted"),
        ("admitted", "running"),
        ("running", "succeeded"),
    ):
        transition(db, row.job_id, before, after)
    db.execute(
        "UPDATE accepted_job SET terminal_at = now() - %s WHERE job_id = %s",
        (terminal_ago, row.job_id),
    )
    name = f"{kind}.bin"
    segment = "s1/" if kind == "checkpoint" else ""
    index_reference(
        db,
        {
            "uri": f"s3://{bucket}/acme/b/{row.job_id}/seed-3/sim/r0a1/{segment}{name}",
            "sha256": "4" * 64,
            "size": 1,
            "name": name,
            "kind": kind,
            "producer": {"job": row.job_id, "task": "sim", "attempt": "r0a1"},
        },
    )
    return row


@pytest.mark.integration
def test_reaper_overdue_counts_rows_past_their_window_within_the_hard_expiry(db):
    assert values(db)["reaper_overdue"] == 0
    finished_job(db, timedelta(days=3))
    assert values(db)["reaper_overdue"] == 0
    overdue = finished_job(db, timedelta(days=8))
    finished_job(db, timedelta(days=30))
    assert values(db)["reaper_overdue"] == 1
    db.execute(
        "INSERT INTO reaper_job (job_id, results_done_at) VALUES (%s, now())", (overdue.job_id,)
    )
    assert values(db)["reaper_overdue"] == 0


@pytest.mark.integration
def test_reaper_overdue_reads_the_checkpoint_window_of_the_bucket(db):
    row = finished_job(db, timedelta(days=2), "wdf-checkpoints", "checkpoint")
    assert values(db)["reaper_overdue"] == 1
    db.execute("INSERT INTO reaper_job (job_id, results_done_at) VALUES (%s, now())", (row.job_id,))
    assert values(db)["reaper_overdue"] == 1
    db.execute("UPDATE reaper_job SET checkpoints_done_at = now()")
    assert values(db)["reaper_overdue"] == 0


@pytest.mark.integration
def test_checkpoint_age_is_the_largest_age_of_the_newest_checkpoint_of_a_live_job(db):
    assert values(db)["checkpoint_age_seconds"] == 0
    old, new = job_row(), job_row()
    for row in (old, new):
        insert_accepted(db, [row])
    for n, (row, ago) in enumerate(((old, 600), (old, 60), (new, 120))):
        index_reference(
            db,
            {
                "uri": f"s3://wdf-checkpoints/acme/b/{row.job_id}/seed-3/sim/r0a1/s{n}/c.json",
                "sha256": "5" * 64,
                "size": 1,
                "name": "c.json",
                "kind": "checkpoint",
                "producer": {"job": row.job_id, "task": "sim", "attempt": "r0a1"},
            },
        )
        db.execute(
            "UPDATE artifact SET created_at = now() - make_interval(secs => %s) WHERE key LIKE %s",
            (ago, f"%/{row.job_id}/%/s{n}/%"),
        )
    assert 118 <= values(db)["checkpoint_age_seconds"] < 160


@pytest.mark.integration
def test_reaper_deleted_counts_the_rows_soft_deleted_in_the_last_hour(db):
    row = finished_job(db, timedelta(days=1))
    assert values(db)["reaper_deleted"] == 0
    db.execute("UPDATE artifact SET deleted_at = now() - interval '2 hours'")
    assert values(db)["reaper_deleted"] == 0
    db.execute("UPDATE artifact SET deleted_at = now() - interval '5 minutes'")
    assert values(db)["reaper_deleted"] == 1
    assert row


@pytest.mark.integration
def test_labelled_source_prints_one_line_per_row_and_needs_its_label_column(db):
    def inputs_of(label: str, query: str):
        return parse(
            yaml.safe_dump(
                {
                    "inputs": [
                        {
                            "name": "per_code",
                            "unit": "failures",
                            "source": {"kind": "sql_labelled", "label": label, "ref": query},
                        }
                    ]
                }
            )
        )

    query = "select * from (values ('a', 2), ('b', 0.5)) as t(code, n)"
    collected = inputs.collect_inputs(db, inputs_of("code", query))
    assert collected.values == [("per_code{code=a}", 2), ("per_code{code=b}", 0.5)]
    with pytest.raises(Refused, match="label-column-missing"):
        inputs.collect_inputs(db, inputs_of("tenant", query))
    with pytest.raises(Refused, match="label-column-missing"):
        inputs.collect_inputs(db, inputs_of("code", "select 'a' as code, 1, 2"))


def test_a_labelled_source_needs_a_label_and_a_label_needs_a_labelled_source():
    labelled = "inputs: [{name: n, unit: u, source: {kind: sql_labelled, ref: x}}]"
    plain = "inputs: [{name: n, unit: u, source: {kind: sql, ref: x, label: code}}]"
    for text in (labelled, plain):
        with pytest.raises(Refused, match="schema-violation"):
            parse(text)
