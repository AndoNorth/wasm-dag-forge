"""Fixtures that run JobWorkflow on a dev server with StaticPlacer, FakeControl on the control
queue of the build and a FakePool per pool."""

import asyncio
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from temporalio.api.deployment.v1 import WorkerDeploymentVersion as ProtoVersion
from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentRequest,
    DescribeWorkerDeploymentVersionRequest,
    SetWorkerDeploymentCurrentVersionRequest,
)
from temporalio.client import Client
from temporalio.common import WorkerDeploymentVersion
from temporalio.service import RPCError
from temporalio.worker import Worker, WorkerDeploymentConfig
from wdf_events import Event, build_event
from wdf_events.models import JobParams
from wdf_jobdoc.plan_models import Plan
from wdf_keys import workflow_id
from wdf_temporal import tracing_interceptor
from wdf_workflow.contract import naming
from wdf_workflow.models import TaskInput
from wdf_workflow.placement import StaticPlacer, place_activity
from wdf_workflow.testing import FakeControl, FakePool, Succeed
from wdf_workflow.testing.control import LoadedPlan

os.environ.setdefault(naming().workflow.build_id_env, "test-build")
BUILD_ID = os.environ[naming().workflow.build_id_env]

from wdf_workflow.job import JobWorkflow  # noqa: E402
from wdf_workflow.taskpath import control_queue  # noqa: E402

HASH = "a" * 64
SOURCE = {
    "uri": f"s3://wdf-sources/sha256/aa/{HASH}",
    "sha256": HASH,
    "size": 2048,
    "name": "bundle.tar.gz",
    "kind": "source",
}
PARAMS = {
    "compile": {"source": SOURCE, "entry": "sim"},
    "simulate": {"workload": "pi", "args": {"n": 1000}, "seed": 7},
    "aggregate": {"aggregation": "digest_list"},
}
RESOURCES = {
    "compile": {"cpu_milli": 1000, "memory_mib": 1024},
    "simulate": {"cpu_milli": 250, "memory_mib": 384},
    "aggregate": {"cpu_milli": 1000, "memory_mib": 1024},
}
OUTPUT = {"compile": "component", "simulate": "result"}


def _plan(tasks: dict[str, tuple[str, list[str]]], max_parallel: int = 8, **params) -> Plan:
    """A bound plan from `{id: (type, depends_on)}`, whose inputs are the declared output of each
    dependency, with `params` overriding the params of a task type."""
    return Plan.model_validate(
        {
            "plan_version": 1,
            "profile": "ci",
            "max_parallel": max_parallel,
            "parameters": {},
            "tasks": [
                {
                    "id": tid,
                    "type": kind,
                    "pool": kind,
                    "depends_on": sorted(deps),
                    "inputs": [
                        {"task": dep, "output": OUTPUT[tasks[dep][0]]} for dep in sorted(deps)
                    ],
                    **RESOURCES[kind],
                    "run_timeout_s": 600,
                    "retries": 1,
                    "checkpoint": False,
                    "allow_partial": False,
                    "params": params.get(kind, PARAMS[kind]),
                }
                for tid, (kind, deps) in tasks.items()
            ],
        }
    )


# A compile, four simulate tasks over it and an aggregate over them.
FAN = {
    "build": ("compile", []),
    "sim-a": ("simulate", ["build"]),
    "sim-b": ("simulate", ["build"]),
    "sim-c": ("simulate", ["build"]),
    "sim-d": ("simulate", ["build"]),
    "agg": ("aggregate", ["sim-a", "sim-b", "sim-c", "sim-d"]),
}


def _accepted(manifest_hash: str | None = None) -> Event:
    """The accepted event of a new job, whose manifest is named by its hash."""
    sha256 = manifest_hash or uuid.uuid4().hex * 2
    return build_event(
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


PARAMS_BLOCK = JobParams(seed=7, parameters={})


class CountingPlacer(StaticPlacer):
    """StaticPlacer that counts its calls."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def place(self, request, escalate=False):
        self.calls += 1
        return super().place(request, escalate)


@dataclass
class Rig:
    """The workers of one test: the workflow worker, the control worker and the pool fakes."""

    client: Client
    control: FakeControl
    placer: CountingPlacer
    pools: dict[str, FakePool] = field(default_factory=dict)

    async def start(self, plan: Plan, event: Event | None = None) -> Any:
        """Starts the job of `plan` as admission would, and returns its handle."""
        event = event or _accepted()
        # FakeControl holds one job row, so the row of a job that ended is claimed again for the
        # next one, as admission claims each new row.
        if self.control.status in ("succeeded", "partial", "failed", "cancelled", "terminated"):
            self.control.status = "admitted"
        self.control.plans[event.data["manifest"]["uri"]] = LoadedPlan(
            plan=plan, max_history_events=10_000
        )
        return await self.client.start_workflow(
            JobWorkflow.run,
            args=[event, PARAMS_BLOCK],
            id=workflow_id(event.jobid),
            task_queue=naming().workflow.queue,
        )


Script = Callable[[TaskInput], Any]


def _rig(
    client: Client,
    scripts: dict[str, Script] | None = None,
    control: FakeControl | None = None,
):
    """An async context of a Rig whose pools run `scripts`, each succeeding by default, and whose
    workers carry the tracing interceptor of wdf_temporal."""
    scripts = {pool: lambda task: Succeed() for pool in naming().pools} | (scripts or {})
    this = Rig(client, control or FakeControl(), CountingPlacer())
    workflow_worker = Worker(
        client,
        task_queue=naming().workflow.queue,
        workflows=[JobWorkflow],
        activities=[place_activity(this.placer), this.control.emit],
        interceptors=[tracing_interceptor()],
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(naming().workflow.deployment, BUILD_ID),
            use_worker_versioning=True,
        ),
    )
    control_worker = Worker(
        client,
        task_queue=control_queue(),
        activities=this.control.control_activities(),
        interceptors=[tracing_interceptor()],
    )
    this.pools = {pool: FakePool(client, pool, script) for pool, script in scripts.items()}
    return Running(this, [workflow_worker, control_worker, *this.pools.values()])


class Running:
    def __init__(self, this: Rig, workers: list):
        self.this = this
        self.workers = workers

    async def __aenter__(self) -> Rig:
        for worker in self.workers:
            await worker.__aenter__()
        await make_current(self.this.client)
        return self.this

    async def __aexit__(self, *exc) -> None:
        for worker in reversed(self.workers):
            await worker.__aexit__(*exc)


async def make_current(client: Client, timeout_s: float = 60) -> None:
    """Makes the test build the current version of the workflow deployment, as a deploy does with
    the raw call, once its pollers are registered, since the SDK has no client method for it."""
    deployment = naming().workflow.deployment
    service = client.workflow_service
    version = DescribeWorkerDeploymentVersionRequest(
        namespace=client.namespace,
        deployment_version=ProtoVersion(deployment_name=deployment, build_id=BUILD_ID),
    )
    for _ in range(int(timeout_s / 0.5)):
        try:
            info = (
                await service.describe_worker_deployment_version(version)
            ).worker_deployment_version_info
            if any(q.name == naming().workflow.queue for q in info.task_queue_infos):
                break
        except RPCError:
            pass
        await asyncio.sleep(0.5)
    described = await service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(namespace=client.namespace, deployment_name=deployment)
    )
    if (
        described.worker_deployment_info.routing_config.current_deployment_version.build_id
        == BUILD_ID
    ):
        return
    await service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=client.namespace,
            deployment_name=deployment,
            build_id=BUILD_ID,
            conflict_token=described.conflict_token,
            identity=client.identity,
        )
    )


@pytest.fixture
def plan():
    return _plan


@pytest.fixture
def fan() -> dict[str, tuple[str, list[str]]]:
    return dict(FAN)


@pytest.fixture
def accepted():
    return _accepted


@pytest.fixture
def rig():
    return _rig
