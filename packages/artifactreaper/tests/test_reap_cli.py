"""`run-once` as an operator runs it: counters, exit codes and a dry run that changes nothing."""

import subprocess
import sys

import pytest
from wdf_storecheck.s3 import dump_credentials

pytestmark = [pytest.mark.integration, pytest.mark.pair]


@pytest.fixture
def run_once(shelf, login, tmp_path):
    credentials = tmp_path / "credentials.json"
    dump_credentials(credentials, shelf.credentials)
    dsn = login.dsn("wdf_reaper")

    def run(*extra: str, dsn: str = dsn) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "wdf_artifactreaper",
                "run-once",
                "--dsn",
                dsn,
                "--endpoint",
                shelf.endpoint,
                "--credentials",
                str(credentials),
                "--now",
                "2026-11-20T12:00:00+00:00",
                *extra,
            ],
            capture_output=True,
            text=True,
        )

    return run


def counters(result: subprocess.CompletedProcess) -> dict[str, int]:
    return {k: int(v) for k, v in (line.split(": ") for line in result.stdout.splitlines())}


def snapshot(world) -> tuple:
    rows = world.db.execute(
        "SELECT row_id, deleted_at, cache_key FROM artifact ORDER BY row_id"
    ).fetchall()
    progress = world.db.execute("SELECT * FROM reaper_job ORDER BY job_id").fetchall()
    return rows, progress, [world.keys(b) for b in ("wdf-results", "wdf-checkpoints")]


def test_reap_dry_run_prints_the_counters_and_changes_no_row_or_object(world, run_once, ago):
    expired = world.job("succeeded", ago(9))
    world.result(expired)
    world.summary(expired)
    world.checkpoint(expired, 0)
    running = world.job("running")
    for segment in range(5):
        world.checkpoint(running, segment)
    before = snapshot(world)

    result = run_once("--dry-run")

    assert result.returncode == 0, result.stderr
    assert counters(result) == {
        "results_jobs": 1,
        "checkpoint_jobs": 1,
        "deleted_objects": 2 + 2 + 3 * 2,
        "kept_live": 2,
        "pruned_checkpoints": 3,
        "expired_cache_entries": 0,
        "tombstoned": 0,
        "purged": 0,
        "orphans": 0,
    }
    assert snapshot(world) == before

    real = run_once()
    assert real.returncode == 0, real.stderr
    assert counters(real) == counters(result)
    assert snapshot(world) != before


def test_reap_run_once_exits_3_when_the_database_is_unreachable(run_once):
    result = run_once(dsn="postgresql://nobody@127.0.0.1:9/none?connect_timeout=2")
    assert result.returncode == 3
    assert result.stdout == ""


def test_reap_run_once_exits_2_on_a_bad_time(run_once):
    result = run_once("--now", "yesterday")
    assert result.returncode == 2
