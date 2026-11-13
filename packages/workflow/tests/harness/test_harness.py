"""The harness command line: smoke jobs that load and bind as load_plan would, a T1 restart of the
workflow worker with tasks in flight, and the T1 refusal of the scenarios that read the job row or
the index."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from wdf_plan import plan_hash, read_plan
from wdf_workflow.activities.cache import bundle_key
from wdf_workflow.harness import ENTRY, SIMULATE_TASKS, smoke_jobs
from wdf_workflow.harness.__main__ import main

LIMIT_S = 300


def harness(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wdf_workflow.harness", *args],
        capture_output=True,
        text=True,
        timeout=LIMIT_S,
        env={**os.environ, "WDF_BUILD_ID": "harness-test"},
    )


def counters(stdout: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in stdout.splitlines() if ": " in line)


def test_smoke_jobs_load_as_load_plan_reads_them():
    jobs = smoke_jobs(4)
    for job in jobs:
        manifest = job.event.data["manifest"]
        assert plan_hash(read_plan(job.manifest)) == manifest["sha256"]
        tasks = [(t.id, t.type) for t in job.loaded().plan.tasks]
        assert tasks[0] == ("build", "compile") and tasks[-1] == ("agg", "aggregate")
        assert sum(kind == "simulate" for _, kind in tasks) == SIMULATE_TASKS
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "bundle.tar"
            source.write_bytes(job.source)
            assert bundle_key(source, ENTRY) is not None
    # Every second job reuses one source, so its compile can hit the cache.
    assert jobs[0].source == jobs[2].source != jobs[1].source != jobs[3].source


@pytest.mark.parametrize(
    "argv",
    [
        ["--address", "127.0.0.1:1", "restart", "--jobs", "1"],
        ["--address", "127.0.0.1:1", "smoke", "--jobs", "0"],
        ["--address", "127.0.0.1:1", "--kube-context", "kind-wdf", "describe"],
    ],
)
def test_usage_errors_exit_2(argv, capsys):
    try:
        code = main(argv)
    except SystemExit as exit:
        code = exit.code
    assert code == 2


@pytest.mark.integration
def test_harness_restart_worker(temporal):
    """Restarts the workflow worker with simulate tasks in flight, and every job finishes."""
    done = harness("--address", temporal, "restart", "--jobs", "4", "--target", "workflow-worker")
    print(done.stdout, done.stderr, sep="\n")
    assert done.returncode == 0
    printed = counters(done.stdout)
    expected = {
        "finished": "4",
        "failed": "0",
        "infra_retries": "0",
        "restarted": "workflow-worker",
    }
    assert expected.items() <= printed.items()
    assert int(printed["in_flight"]) >= 1


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["pause-resume", "zombie"])
def test_harness_t1_refuses_row_scenarios(temporal, scenario):
    done = harness("--address", temporal, "smoke", "--jobs", "1", "--scenario", scenario)
    print(f"exit {done.returncode}", done.stderr, sep="\n")
    assert done.returncode == 2
    assert done.stdout == ""
    assert f"refused: {scenario} reads the job row or the index" in done.stderr
