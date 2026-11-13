"""The harness command line: smoke jobs that load and bind as load_plan would, a T1 restart of the
workflow worker with tasks in flight, and the T1 refusal of the scenarios that read the job row or
the index, and the status query that the counters read, asked again while it times out."""

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.api.common.v1 import SearchAttributes
from temporalio.converter import encode_search_attributes
from temporalio.service import RPCError, RPCStatusCode
from wdf_keys import workflow_id
from wdf_plan import plan_hash, read_plan
from wdf_workflow.activities.cache import bundle_key
from wdf_workflow.budgets import INFRA_RETRIES
from wdf_workflow.contract import naming
from wdf_workflow.harness import (
    ENTRY,
    SIMULATE_TASKS,
    Counters,
    infra_retries,
    search_attributes,
    smoke_jobs,
)
from wdf_workflow.harness.__main__ import main
from wdf_workflow.models import StatusQuery

LIMIT_S = 300
# The infrastructure budget that the one task of STATUS spent.
SPENT = 2
STATUS = {
    "job_status": "running",
    "pause_reason": None,
    "resume_counter": 0,
    "control_seq": 0,
    "tasks": [
        {
            "id": "sim-1",
            "state": "running",
            "task_attempt": 3,
            "guest_left": 1,
            "infra_left": INFRA_RETRIES - SPENT,
            "reschedules": 0,
            "last_class": None,
            "checkpoint": None,
        }
    ],
}


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


def test_start_options_encode_every_search_attribute():
    [job] = smoke_jobs(1)
    encoded = SearchAttributes()
    encode_search_attributes(search_attributes(job.event), encoded)
    assert set(encoded.indexed_fields) == set(naming().search_attributes)


@pytest.mark.parametrize(
    "argv",
    [
        ["--address", "127.0.0.1:1", "restart", "--jobs", "1"],
        ["--address", "127.0.0.1:1", "smoke", "--jobs", "0"],
        ["--address", "127.0.0.1:1", "--kube-context", "kind-wdf", "describe"],
        ["--address", "127.0.0.1:1", "restart", "--target", ","],
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


class Handle:
    """A workflow handle whose status query times out `timeouts` times before it answers."""

    def __init__(self, timeouts: int, status: RPCStatusCode = RPCStatusCode.CANCELLED):
        self.timeouts = timeouts
        self.status = status
        self.asked = 0

    async def query(self, name, result_type):
        self.asked += 1
        if self.asked <= self.timeouts:
            raise RPCError("Timeout expired", self.status, b"")
        return StatusQuery.model_validate(STATUS)


class Client:
    def __init__(self, handles: dict[str, Handle]):
        self.handles = handles

    def get_workflow_handle(self, workflow: str) -> Handle:
        return self.handles[workflow]


def retried_pool(*job_ids: str) -> SimpleNamespace:
    calls = [
        SimpleNamespace(request=SimpleNamespace(jobid=job_id), task_attempt=2) for job_id in job_ids
    ]
    return SimpleNamespace(calls=calls)


def test_status_query_timeout_is_retried():
    """A status query that times out is asked again, and the counters still come out."""
    slow = Handle(timeouts=2)
    client = Client({workflow_id("slow"): slow})
    counters = Counters()
    asyncio.run(infra_retries(counters, client, {"simulate": retried_pool("slow")}))
    assert slow.asked == 3
    assert counters.infra_retries == SPENT
    assert "status_unanswered" not in counters.extra


def test_status_query_that_never_answers_is_counted():
    client = Client({workflow_id("dead"): Handle(timeouts=10**9), workflow_id("ok"): Handle(0)})
    counters = Counters()
    asyncio.run(infra_retries(counters, client, {"simulate": retried_pool("dead", "ok")}, 0))
    assert counters.infra_retries == SPENT
    assert counters.extra["status_unanswered"] == 1
    assert "status_unanswered: 1" in counters.lines()


def test_status_query_other_error_is_raised():
    client = Client({workflow_id("gone"): Handle(1, RPCStatusCode.NOT_FOUND)})
    with pytest.raises(RPCError):
        asyncio.run(infra_retries(Counters(), client, {"simulate": retried_pool("gone")}))
