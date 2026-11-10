"""The batch bench counts its rows and events, and a second run finds every key taken."""

import subprocess
import sys
from datetime import timedelta

import psycopg
import pytest
from wdf_eventstore import insert_events
from wdf_stateops import bench

pytestmark = pytest.mark.integration

JOBS = 2000


def lines(text: str) -> list[str]:
    return text.splitlines()


def test_bench_inserts_every_row_and_event_once_and_then_nothing(appdb):
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "wdf_stateops.bench",
            "--jobs",
            str(JOBS),
            "--dsn",
            appdb,
            "--twice",
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    out = lines(run.stdout)
    first, second = out.index("run: 1"), out.index("run: 2")
    assert out[first + 1 : first + 5] == [
        "rows_inserted: 2000",
        "rows_duplicates: 0",
        "events_inserted: 28000",
        "events_duplicates: 0",
    ]
    assert out[second + 1 : second + 5] == [
        "rows_inserted: 0",
        "rows_duplicates: 2000",
        "events_inserted: 0",
        "events_duplicates: 28000",
    ]
    assert "insert_chunks: 2" in out
    assert any(line.startswith("accepted_job_bytes: ") for line in out)
    assert any(line.startswith("event_chunks: ") for line in out)


def test_a_repeat_invocation_against_the_same_database_inserts_nothing(appdb, capsys):
    assert bench.main(["--jobs", "300", "--dsn", appdb]) == 0
    capsys.readouterr()
    assert bench.main(["--jobs", "300", "--dsn", appdb]) == 0
    out = lines(capsys.readouterr().out)
    assert "rows_inserted: 0" in out and "events_inserted: 0" in out


def test_the_origin_is_today_on_the_database_clock(appdb):
    """A calendar date here would be dropped by the retention policy once it aged past it."""
    with psycopg.connect(appdb) as conn:
        (database_now,) = conn.execute("SELECT now()").fetchone()
    assert timedelta(0) <= database_now - bench.ORIGIN < timedelta(days=1)


def test_bench_exits_1_when_a_count_is_wrong(appdb, monkeypatch, capsys):
    """A store that drops one event is reported by the counts of the run and by the stored rows."""

    dropped = []

    def dropping(conn, events):
        events = list(events)
        if not dropped:
            dropped.append(events.pop())
        return insert_events(conn, events)

    monkeypatch.setattr(bench, "insert_events", dropping)
    assert bench.main(["--jobs", "10", "--dsn", appdb]) == 1
    err = capsys.readouterr().err
    assert "wrong: run 1: 139 events seen, expected 140" in err
    assert "wrong: 139 events stored, expected 140" in err


def test_bench_rejects_a_job_count_below_one(capsys):
    with pytest.raises(SystemExit) as error:
        bench.main(["--jobs", "0", "--dsn", "unused"])
    assert error.value.code == 2


def test_the_events_of_a_job_are_fourteen_with_distinct_keys():
    events = bench.job_events(7, bench.job_row(7))
    assert len(events) == bench.EVENTS_PER_JOB
    assert len({e.id for e in events}) == len(events)
    assert events == bench.job_events(7, bench.job_row(7))
