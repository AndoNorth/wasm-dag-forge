"""Fixtures that run the worker service on a dev server, with FakeControl standing in for the
control activities' database and store, a fake reaper pass, and a FakePool on the compile queue."""

import asyncio
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from temporalio.client import Client, WorkflowHandle
from temporalio.common import PinnedVersioningOverride, WorkerDeploymentVersion
from temporalio.service import RPCError
from wdf_events import build_event
from wdf_events.models import JobParams
from wdf_jobdoc.plan_models import Plan
from wdf_keys import workflow_id
from wdf_workflow.contract import naming
from wdf_workflow.job import JobWorkflow
from wdf_workflow.service import Config, serve
from wdf_workflow.testing import FakeControl
from wdf_workflow.testing.control import LoadedPlan

HASH = "a" * 64
# One compile task, whose path runs a control activity of each kind it needs on the control
# queue: load_plan, cache_lookup, commit and cache_record.
PLAN = Plan.model_validate(
    {
        "plan_version": 1,
        "profile": "ci",
        "max_parallel": 1,
        "parameters": {},
        "tasks": [
            {
                "id": "build",
                "type": "compile",
                "pool": "compile",
                "depends_on": [],
                "inputs": [],
                "cpu_milli": 1000,
                "memory_mib": 1024,
                "run_timeout_s": 600,
                "retries": 1,
                "checkpoint": False,
                "allow_partial": False,
                "params": {
                    "source": {
                        "uri": f"s3://wdf-sources/sha256/aa/{HASH}",
                        "sha256": HASH,
                        "size": 2048,
                        "name": "bundle.tar.gz",
                        "kind": "source",
                    },
                    "entry": "sim",
                },
            }
        ],
    }
)
REAP_REPORT = {"ran": True, "objects": 0}


def fake_pass(heartbeat) -> dict:
    heartbeat()
    return REAP_REPORT


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def config(temporal, monkeypatch):
    """The configuration of a service of `build_id`, whose stores and databases are never reached
    because FakeControl serves the control activities, with the build id in the environment,
    where the workflow reads its control queue."""

    def make(build_id: str) -> Config:
        monkeypatch.setenv(naming().workflow.build_id_env, build_id)
        return Config(
            temporal_address=temporal,
            namespace=naming().namespace,
            build_id=build_id,
            metrics_address=f"127.0.0.1:{free_port()}",
            workflow_dsn="postgresql://unused",
            artifacts_dsn="postgresql://unused",
            store_endpoint="http://127.0.0.1:1",
            workflow_credentials="/unused",
            indexer_credentials="/unused",
        )

    return make


@asynccontextmanager
async def _running(config: Config, control: FakeControl):
    """The service in a task of this loop, stopped and drained on exit, yielding the task."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        serve(config, control.control_activities(), control.emit, fake_pass, stop=stop)
    )
    try:
        yield task
    finally:
        stop.set()
        await task


async def _start_job(client: Client, control: FakeControl, build_id: str) -> WorkflowHandle:
    """Starts a job of PLAN pinned to the build, as admission would once the build is current."""
    sha256 = uuid.uuid4().hex * 2
    accepted = build_event(
        "job.accepted",
        source="/wdf/dispatch",
        tenant="acme",
        jobid=str(uuid.uuid4()),
        priorityclass="standard",
        jobclass="simulate",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
        data={
            "manifest": {
                "uri": f"s3://wdf-sources/sha256/{sha256[:2]}/{sha256}",
                "sha256": sha256,
                "size": 2048,
                "name": "manifest.json",
                "kind": "manifest",
            },
            "batch_id": str(uuid.uuid4()),
            "submission_key": f"sub-{uuid.uuid4()}",
        },
    )
    control.plans[accepted.data["manifest"]["uri"]] = LoadedPlan(
        plan=PLAN, max_history_events=10_000
    )
    # The server refuses a pin to a version until the build's pollers have registered it.
    for _ in range(120):
        try:
            return await client.start_workflow(
                JobWorkflow.run,
                args=[accepted, JobParams(seed=7, parameters={})],
                id=workflow_id(accepted.jobid),
                task_queue=naming().workflow.queue,
                versioning_override=PinnedVersioningOverride(
                    WorkerDeploymentVersion(naming().workflow.deployment, build_id)
                ),
            )
        except RPCError as error:
            if "is not present in task queue" not in str(error):
                raise
            await asyncio.sleep(0.5)
    raise TimeoutError(f"build {build_id} never polled {naming().workflow.queue}")


@pytest.fixture
def running():
    return _running


@pytest.fixture
def start_job():
    return _start_job


@pytest.fixture
def reap_report() -> dict:
    return REAP_REPORT
