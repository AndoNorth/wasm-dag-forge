"""The rollout of a build of the workflow worker: making it current, counting the jobs still pinned
to an old build, moving an open job to a fixed build, and deleting an old build once it is drained.

SDK 1.35.0 has no client method for worker deployments, so each step is a raw call of the workflow
service. Every job is pinned and finishes on the build that started it, so an old build runs until
no open job is pinned to it: its count of open pinned jobs is 0. The server's own drainage status
lags that count by minutes and is reported beside it, never waited on.
"""

import asyncio
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from google.protobuf.field_mask_pb2 import FieldMask
from temporalio.api.deployment.v1 import WorkerDeploymentVersion as ProtoVersion
from temporalio.api.enums.v1 import EventType, VersionDrainageStatus, WorkflowTaskFailedCause
from temporalio.api.workflow.v1 import WorkflowExecutionOptions
from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentRequest,
    DescribeWorkerDeploymentVersionRequest,
    SetWorkerDeploymentCurrentVersionRequest,
    UpdateWorkflowExecutionOptionsRequest,
)
from temporalio.client import Client
from temporalio.common import PinnedVersioningOverride, WorkerDeploymentVersion
from temporalio.service import RPCError, RPCStatusCode

from .contract import naming

# Where each build runs as its own Deployment, workflow-worker-<build id>.
KUBE_NAMESPACE = "wdf-control"
# A build's first pollers reach the server within seconds of its pod starting.
POLLERS_S = 120.0
# The only field of the options a move changes; any other path is refused or silently ignored.
OVERRIDE_MASK = "versioning_override"

MoveState = Literal["overridden", "moved", "reverted"]


class NotDrained(Exception):
    def __init__(self, drainage: "Drainage"):
        super().__init__(f"build {drainage.build_id} has {drainage.pinned} open pinned jobs")
        self.drainage = drainage


@dataclass(frozen=True)
class Drainage:
    build_id: str
    # The open jobs pinned to the build, which a move to another build no longer counts.
    pinned: int
    # The server's drainage status, `draining`, `drained` or `unspecified` for a current build.
    server_status: str

    @property
    def drained(self) -> bool:
        return self.pinned == 0


@dataclass(frozen=True)
class Move:
    workflow_id: str
    from_build: str
    to_build: str
    # `overridden` once the override landed and before the job's next workflow task, `moved` once
    # a task completed on `to_build`, and `reverted` when that build could not replay the history
    # and the job was put back on `from_build`.
    state: MoveState


def _version(build_id: str) -> ProtoVersion:
    return ProtoVersion(deployment_name=naming().workflow.deployment, build_id=build_id)


async def wait_pollers(client: Client, build_id: str, timeout_s: float = POLLERS_S) -> None:
    """Waits until the server has seen the build's pollers on the workflow queue."""
    request = DescribeWorkerDeploymentVersionRequest(
        namespace=client.namespace, deployment_version=_version(build_id)
    )
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            info = (
                await client.workflow_service.describe_worker_deployment_version(request)
            ).worker_deployment_version_info
            if any(q.name == naming().workflow.queue for q in info.task_queue_infos):
                return
        except RPCError as error:
            # The version, or the whole deployment on a first deploy, is not there before them.
            if error.status != RPCStatusCode.NOT_FOUND:
                raise
        if time.monotonic() > deadline:
            raise TimeoutError(f"build {build_id} never polled {naming().workflow.queue}")
        await asyncio.sleep(0.5)


async def current(client: Client) -> str | None:
    """The current build of the workflow deployment, or None before any build was made current."""
    try:
        described = await client.workflow_service.describe_worker_deployment(
            DescribeWorkerDeploymentRequest(
                namespace=client.namespace, deployment_name=naming().workflow.deployment
            )
        )
    except RPCError as error:
        if error.status == RPCStatusCode.NOT_FOUND:
            return None
        raise
    routing = described.worker_deployment_info.routing_config
    return routing.current_deployment_version.build_id or None


async def set_current(client: Client, build_id: str, timeout_s: float = POLLERS_S) -> str | None:
    """Makes the build current once its pollers are seen, and returns the build that was current
    before, or None on a first deploy.

    Run at every deploy, the first included, since a deployment with no current version gives a new
    job to no build. The call carries the deployment's conflict token, so a change made since the
    describe is refused, and never `allow_no_pollers`, so a build that does not poll is never made
    current. A build that is current already is left as it is.
    """
    await wait_pollers(client, build_id, timeout_s)
    service = client.workflow_service
    described = await service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(
            namespace=client.namespace, deployment_name=naming().workflow.deployment
        )
    )
    previous = described.worker_deployment_info.routing_config.current_deployment_version.build_id
    if previous == build_id:
        return previous
    await service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=client.namespace,
            deployment_name=naming().workflow.deployment,
            build_id=build_id,
            conflict_token=described.conflict_token,
            identity=client.identity,
        )
    )
    return previous or None


def pinned_query(build_id: str) -> str:
    """The visibility query of the open jobs pinned to a build, whose search attribute names the
    version with a colon where the SDK's canonical string has a dot."""
    return (
        f"TemporalWorkerDeploymentVersion = '{naming().workflow.deployment}:{build_id}'"
        " AND TemporalWorkflowVersioningBehavior = 'Pinned' AND ExecutionStatus = 'Running'"
    )


async def drainage(client: Client, build_id: str) -> Drainage:
    """The count of open jobs pinned to the build, paused ones included, and the server's status."""
    pinned = (await client.count_workflows(pinned_query(build_id))).count
    try:
        info = (
            await client.workflow_service.describe_worker_deployment_version(
                DescribeWorkerDeploymentVersionRequest(
                    namespace=client.namespace, deployment_version=_version(build_id)
                )
            )
        ).worker_deployment_version_info
        status = VersionDrainageStatus.Name(info.drainage_info.status)
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        status = "VERSION_DRAINAGE_STATUS_UNSPECIFIED"
    return Drainage(build_id, pinned, status.removeprefix("VERSION_DRAINAGE_STATUS_").lower())


async def _override(client: Client, workflow_id: str, build_id: str) -> None:
    """Pins the open job to the build, and reads it back, since a mask the server does not apply
    returns OK and changes nothing."""
    pinned = PinnedVersioningOverride(
        WorkerDeploymentVersion(naming().workflow.deployment, build_id)
    )
    await client.workflow_service.update_workflow_execution_options(
        UpdateWorkflowExecutionOptionsRequest(
            namespace=client.namespace,
            workflow_execution={"workflow_id": workflow_id},
            workflow_execution_options=WorkflowExecutionOptions(
                versioning_override=pinned._to_proto()
            ),
            update_mask=FieldMask(paths=[OVERRIDE_MASK]),
            identity=client.identity,
        )
    )
    described = await client.get_workflow_handle(workflow_id).describe()
    info = described.raw_description.workflow_execution_info.versioning_info
    if info.versioning_override.pinned.version.build_id != build_id:
        raise RuntimeError(f"the override of {workflow_id} to {build_id} did not apply")


def _since_override(history: list, build_id: str) -> tuple[str, list] | None:
    """The build of the last task completed before the latest override to `build_id`, and the
    events after that override, or None when the latest override is not to `build_id`."""
    for index in range(len(history) - 1, -1, -1):
        event = history[index]
        if event.event_type != EventType.EVENT_TYPE_WORKFLOW_EXECUTION_OPTIONS_UPDATED:
            continue
        attributes = event.workflow_execution_options_updated_event_attributes
        if not attributes.HasField("versioning_override"):
            continue
        if attributes.versioning_override.pinned.version.build_id != build_id:
            return None
        before = [
            e.workflow_task_completed_event_attributes.deployment_version.build_id
            for e in history[:index]
            if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
        ]
        # A job that never completed a task has no build to go back to.
        return (before[-1] if before else ""), history[index + 1 :]
    return None


def _outcome(after: list, build_id: str) -> Literal["moved", "failed"] | None:
    """`moved` when a task completed on the build after the override, `failed` when the build's
    first task failed to replay the history, or None before either."""
    for event in after:
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED:
            attributes = event.workflow_task_completed_event_attributes
            # A task already running on the old build when the override landed completes there.
            if attributes.deployment_version.build_id == build_id:
                return "moved"
        elif event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED:
            cause = event.workflow_task_failed_event_attributes.cause
            if cause == WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR:
                return "failed"
    return None


async def move(client: Client, workflow_id: str, build_id: str, wait_s: float = 30) -> Move:
    """Moves an open pinned job, a paused one included, to a fixed build, and waits at most `wait_s`
    for its next workflow task.

    The fixed build replays the whole history, so its change is guarded by `workflow.patched`. The
    override lands at once and the job leaves the old build's count, but the job runs on the fixed
    build only at its next task, which a job waiting on a signal, such as a paused job, gets only
    with that signal: until then the move is `overridden`, and a later call with the same build
    sends nothing new and waits again. A first task on the fixed build that fails to replay the
    history leaves the job running and stuck, so the job is pinned back to the build it ran on, and
    the move is `reverted`.
    """
    handle = client.get_workflow_handle(workflow_id)
    history = [event async for event in handle.fetch_history_events()]
    if _since_override(history, build_id) is None:
        await _override(client, workflow_id, build_id)
    deadline = time.monotonic() + wait_s
    while True:
        # The whole history is fetched at each poll, which suits a move of one job at a time.
        history = [event async for event in handle.fetch_history_events()]
        if (since := _since_override(history, build_id)) is None:
            raise RuntimeError(f"the override of {workflow_id} to {build_id} was replaced")
        from_build, after = since
        state = _outcome(after, build_id)
        if state == "moved":
            return Move(workflow_id, from_build, build_id, "moved")
        if state == "failed":
            if not from_build:
                raise RuntimeError(f"{workflow_id} fails on {build_id} and never ran elsewhere")
            await _override(client, workflow_id, from_build)
            return Move(workflow_id, from_build, build_id, "reverted")
        if time.monotonic() >= deadline:
            return Move(workflow_id, from_build, build_id, "overridden")
        await asyncio.sleep(0.5)


def kubectl_delete(kube_context: str) -> Callable[[str], None]:
    """Deletes the Deployment of a build in the cluster of the kube context."""

    def delete(build_id: str) -> None:
        subprocess.run(
            [
                "kubectl",
                f"--context={kube_context}",
                f"--namespace={KUBE_NAMESPACE}",
                "delete",
                "deployment",
                f"workflow-worker-{build_id}",
                "--ignore-not-found",
                "--wait",
            ],
            check=True,
        )

    return delete


async def retire(client: Client, build_id: str, delete: Callable[[str], None]) -> Drainage:
    """Deletes the old build's Deployment with `delete`, only when no open job is pinned to it, and
    raises NotDrained otherwise. The current build is never retired, checked again after the count
    so a build made current meanwhile is kept."""
    if await current(client) == build_id:
        raise ValueError(f"build {build_id} is current")
    drained = await drainage(client, build_id)
    if not drained.drained:
        raise NotDrained(drained)
    if await current(client) == build_id:
        raise ValueError(f"build {build_id} is current")
    await asyncio.to_thread(delete, build_id)
    return drained
