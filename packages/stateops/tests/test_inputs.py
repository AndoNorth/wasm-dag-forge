"""The inputs of contracts/state/inputs.yaml against a real database and a stand-in store."""

import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from wdf_events import build_event
from wdf_eventstore import insert_events
from wdf_stateops import inputs
from wdf_stateops.inputs import Store, StoreUnavailable, load

SQL_INPUTS = [i.name for i in load().inputs if i.source.kind == "sql"]


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
    assert [line.split()[0] for line in lines if not line.startswith("skipped")] == SQL_INPUTS + [
        "drift_rows"
    ]
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
