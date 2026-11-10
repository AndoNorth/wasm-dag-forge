"""Versioning spike: pinned workflows, the raw deployment calls, the override and the drained count.

Starts the pinned `temporalio/temporal` dev server in one container through wdf_itest, runs
builds A, B and C of `builds.py` as versioned workers of one deployment and an unversioned pool
worker in this process, and prints one `figure <name> <value>` line for every number and finding
of docs/delivery/spikes/S9/versioning.md.
Run in the dev shell from the repository root:
  nix develop -c uv run python docs/delivery/spikes/S9/code/spike.py [out-dir]
Exit 0 the run completed (the verdict is a figure), 1 a step failed.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from builds import BUILDS, POOL_QUEUE, served, step
from google.protobuf.field_mask_pb2 import FieldMask
from temporalio.api.deployment.v1 import WorkerDeploymentVersion as ProtoVersion
from temporalio.api.enums.v1 import (
    EventType,
    VersionDrainageStatus,
    VersioningBehavior,
    WorkerDeploymentVersionStatus,
    WorkflowTaskFailedCause,
)
from temporalio.api.workflow.v1 import WorkflowExecutionOptions
from temporalio.api.workflowservice.v1 import (
    DescribeWorkerDeploymentRequest,
    DescribeWorkerDeploymentVersionRequest,
    GetSystemInfoRequest,
    SetWorkerDeploymentCurrentVersionRequest,
    UpdateWorkflowExecutionOptionsRequest,
)
from temporalio.client import Client, WorkflowHandle
from temporalio.common import PinnedVersioningOverride, WorkerDeploymentVersion
from temporalio.service import RPCError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker, WorkerDeploymentConfig
from wdf_itest import Session

DEPLOYMENT = "wdf-workflow-worker"
QUEUE = "wdf-workflow"
NAMESPACE = "default"
DRAIN_TIMEOUT_S = 20 * 60

out = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="wdf-s9."))
out.mkdir(parents=True, exist_ok=True)
figures = (out / "figures.txt").open("w")


def figure(name: str, value) -> None:
    line = f"figure {name} {value}"
    print(line, flush=True)
    figures.write(line + "\n")
    figures.flush()


def say(text: str) -> None:
    print(f"[{time.strftime('%T')}] {text}", file=sys.stderr, flush=True)


def canonical(build: str) -> str:
    return WorkerDeploymentVersion(DEPLOYMENT, build).to_canonical_string()


def proto_version(build: str) -> ProtoVersion:
    return ProtoVersion(deployment_name=DEPLOYMENT, build_id=build)


def worker(client: Client, build: str) -> Worker:
    return Worker(
        client,
        task_queue=QUEUE,
        workflows=[BUILDS[build]],
        workflow_runner=UnsandboxedWorkflowRunner(),
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(DEPLOYMENT, build), use_worker_versioning=True
        ),
    )


async def wait_version(client: Client, build: str, timeout: float = 30) -> None:
    """Waits until the server has registered the build's pollers on the workflow queue."""
    deadline = time.monotonic() + timeout
    request = DescribeWorkerDeploymentVersionRequest(
        namespace=NAMESPACE, deployment_version=proto_version(build)
    )
    while True:
        try:
            info = await client.workflow_service.describe_worker_deployment_version(request)
            if any(q.name == QUEUE for q in info.worker_deployment_version_info.task_queue_infos):
                return
        except RPCError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"build {build} never registered")
        await asyncio.sleep(0.5)


async def describe_version(client: Client, build: str):
    return (
        await client.workflow_service.describe_worker_deployment_version(
            DescribeWorkerDeploymentVersionRequest(
                namespace=NAMESPACE, deployment_version=proto_version(build)
            )
        )
    ).worker_deployment_version_info


async def set_current(client: Client, build: str, name: str) -> None:
    described = await client.workflow_service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(namespace=NAMESPACE, deployment_name=DEPLOYMENT)
    )
    previous = described.worker_deployment_info.routing_config.current_deployment_version.build_id
    response = await client.workflow_service.set_worker_deployment_current_version(
        SetWorkerDeploymentCurrentVersionRequest(
            namespace=NAMESPACE,
            deployment_name=DEPLOYMENT,
            build_id=build,
            conflict_token=described.conflict_token,
            identity=client.identity,
        )
    )
    after = await client.workflow_service.describe_worker_deployment(
        DescribeWorkerDeploymentRequest(namespace=NAMESPACE, deployment_name=DEPLOYMENT)
    )
    routing = after.worker_deployment_info.routing_config
    figure(f"{name}.previous_current", previous or "none")
    figure(
        f"{name}.response_previous_build", response.previous_deployment_version.build_id or "none"
    )
    figure(f"{name}.current", routing.current_deployment_version.build_id)
    figure(f"{name}.token_changed", after.conflict_token != described.conflict_token)
    figure(f"{name}.last_modifier", after.worker_deployment_info.last_modifier_identity)
    figure(f"{name}.pass", routing.current_deployment_version.build_id == build)


async def override(client: Client, handle: WorkflowHandle, build: str, paths: list[str]) -> str:
    """Sends the raw options update with a pinned override, returning `ok` or the server error."""
    pinned = PinnedVersioningOverride(WorkerDeploymentVersion(DEPLOYMENT, build))._to_proto()
    try:
        await client.workflow_service.update_workflow_execution_options(
            UpdateWorkflowExecutionOptionsRequest(
                namespace=NAMESPACE,
                workflow_execution={"workflow_id": handle.id},
                workflow_execution_options=WorkflowExecutionOptions(versioning_override=pinned),
                update_mask=FieldMask(paths=paths),
                identity=client.identity,
            )
        )
        return "ok"
    except RPCError as exc:
        return f"{exc.status.name}: {exc.message}".replace(" ", "_")


async def events(handle: WorkflowHandle) -> list:
    return [e async for e in handle.fetch_history_events()]


def names(history: list) -> list[str]:
    return [EventType.Name(e.event_type).removeprefix("EVENT_TYPE_") for e in history]


async def count(client: Client, query: str) -> int:
    return (await client.count_workflows(query)).count


def pinned_running(build: str) -> str:
    return (
        f"TemporalWorkerDeploymentVersion = '{DEPLOYMENT}:{build}' AND "
        "TemporalWorkflowVersioningBehavior = 'Pinned' AND ExecutionStatus = 'Running'"
    )


async def versioning_info(handle: WorkflowHandle) -> dict:
    info = (await handle.describe()).raw_description.workflow_execution_info.versioning_info
    return {
        "behavior": VersioningBehavior.Name(info.behavior),
        "version": info.deployment_version.build_id,
        "override": info.versioning_override.pinned.version.build_id,
    }


async def start(client: Client, name: str) -> WorkflowHandle:
    return await client.start_workflow(
        "JobWorkflow", id=f"s9-{name}-{uuid.uuid4().hex[:6]}", task_queue=QUEUE
    )


async def wait_waiting(handle: WorkflowHandle, timeout: float = 30) -> None:
    """Waits until the workflow has completed its prepare step and blocks on the signal."""
    deadline = time.monotonic() + timeout
    while True:
        if names(await events(handle)).count("WORKFLOW_TASK_COMPLETED") >= 2:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"{handle.id} never reached the signal wait")
        await asyncio.sleep(0.3)


async def spike(address: str) -> None:
    # The port accepts connections a moment before the frontend answers.
    for attempt in range(60):
        try:
            client = await Client.connect(address, namespace=NAMESPACE, identity="s9-spike")
            break
        except RuntimeError:
            if attempt == 59:
                raise
            await asyncio.sleep(1)
    system = await client.workflow_service.get_system_info(GetSystemInfoRequest())
    figure("server.version", system.server_version)
    figure("sdk.temporalio", __import__("temporalio").__version__)

    pool = Worker(client, task_queue=POOL_QUEUE, activities=[step])
    workers = {build: worker(client, build) for build in BUILDS}
    runs = [asyncio.create_task(w.run()) for w in [pool, *workers.values()]]
    try:
        await wait_version(client, "A")

        # A deployment whose only versions have pollers but no current version.
        early = await start(client, "no-current")
        await asyncio.sleep(3)
        figure(
            "no_current.completed_tasks",
            names(await events(early)).count("WORKFLOW_TASK_COMPLETED"),
        )

        # The raw call makes A current on the fresh deployment.
        await set_current(client, "A", "current_a")
        await asyncio.sleep(3)
        figure(
            "no_current.completed_tasks_after_a",
            names(await events(early)).count("WORKFLOW_TASK_COMPLETED"),
        )
        await early.terminate("superseded")

        stays, moved = await start(client, "stays-on-a"), await start(client, "moved-to-b")
        await wait_waiting(stays)
        await wait_waiting(moved)
        figure("count.pinned_a.before_b", await count(client, pinned_running("A")))

        # Then B, after its pollers are seen.
        await wait_version(client, "B")
        await set_current(client, "B", "current_b")
        on_b = await start(client, "started-on-b")
        await wait_waiting(on_b)

        # The override of an open pinned workflow, and the mask path the SDK source leaves open.
        before = names(await events(moved))
        figure("override.mask.empty", await override(client, on_b, "B", []))
        figure(
            "override.mask.empty_describe",
            json.dumps(await versioning_info(on_b), separators=(",", ":")),
        )
        figure("override.mask.camel", await override(client, on_b, "B", ["versioningOverride"]))
        figure("override.mask.snake", await override(client, moved, "B", ["versioning_override"]))
        figure(
            "override.visibility.a_now",
            await count(client, f"WorkflowId = '{moved.id}' AND " + pinned_running("A")),
        )
        figure(
            "override.visibility.b_now",
            await count(client, f"WorkflowId = '{moved.id}' AND " + pinned_running("B")),
        )
        figure(
            "override.describe_now", json.dumps(await versioning_info(moved), separators=(",", ":"))
        )
        await asyncio.sleep(5)
        after = names(await events(moved))
        figure("override.new_events", ",".join(after[len(before) :]) or "none")
        figure(
            "override.visibility.a_5s",
            await count(client, f"WorkflowId = '{moved.id}' AND " + pinned_running("A")),
        )
        figure(
            "override.visibility.b_5s",
            await count(client, f"WorkflowId = '{moved.id}' AND " + pinned_running("B")),
        )
        figure("count.pinned_a.after_override", await count(client, pinned_running("A")))

        await moved.signal("go")
        result = await moved.result()
        figure("moved.result", json.dumps(result, separators=(",", ":")))
        figure(
            "moved.pass",
            result["code"] == "B"
            and result["steps"][-1] == "finish-fixed"
            and result["versions"][-1] == canonical("B"),
        )
        figure("moved.marker_events", names(await events(moved)).count("MARKER_RECORDED"))

        await on_b.signal("go")
        result = await on_b.result()
        figure("on_b.result", json.dumps(result, separators=(",", ":")))

        # The last pinned workflow on A finishes there while B is current.
        await stays.signal("go")
        result = await stays.result()
        closed = time.monotonic()
        figure("stays.result", json.dumps(result, separators=(",", ":")))
        figure("stays.pass", result["code"] == "A" and set(result["versions"]) == {canonical("A")})
        figure("count.pinned_a.after_close", await count(client, pinned_running("A")))
        figure("count.pinned_b.after_close", await count(client, pinned_running("B")))

        # The unguarded change: a workflow on B moved to C, whose code differs before the signal.
        broken = await start(client, "moved-to-c")
        await wait_waiting(broken)
        await wait_version(client, "C")
        figure("unguarded.override", await override(client, broken, "C", ["versioning_override"]))
        await broken.signal("go")
        await asyncio.sleep(10)
        history = await events(broken)
        failed = [e for e in history if e.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED]
        figure("unguarded.status", (await broken.describe()).status.name)
        figure("unguarded.task_failed_events", len(failed))
        if failed:
            attributes = failed[0].workflow_task_failed_event_attributes
            figure("unguarded.cause", WorkflowTaskFailedCause.Name(attributes.cause))
            figure(
                "unguarded.message",
                attributes.failure.message.splitlines()[0].replace(" ", "_")[:160],
            )
        info = await versioning_info(broken)
        figure("unguarded.describe", json.dumps(info, separators=(",", ":")))
        figure("unguarded.back_to_b", await override(client, broken, "B", ["versioning_override"]))
        try:
            result = await asyncio.wait_for(broken.result(), 60)
            figure("unguarded.recovered_result", json.dumps(result, separators=(",", ":")))
        except TimeoutError:
            figure("unguarded.recovered_result", "timeout")

        # One unversioned pool worker served the activities of every build.
        figure("pool.served", json.dumps(dict(served), separators=(",", ":")))
        figure("pool.pass", served["A"] > 0 and served["B"] > 0)

        # The server's drainage status of A, polled until drained.
        while True:
            info = await describe_version(client, "A")
            status = VersionDrainageStatus.Name(info.drainage_info.status)
            if status == "VERSION_DRAINAGE_STATUS_DRAINED":
                figure("drain.a.seconds_after_close", round(time.monotonic() - closed))
                figure("drain.a.version_status", WorkerDeploymentVersionStatus.Name(info.status))
                break
            if time.monotonic() - closed > DRAIN_TIMEOUT_S:
                figure("drain.a.seconds_after_close", f"timeout_{status}")
                break
            await asyncio.sleep(5)
        figure("count.pinned_a.at_drained", await count(client, pinned_running("A")))
    finally:
        for w in [pool, *workers.values()]:
            await w.shutdown()
        await asyncio.gather(*runs, return_exceptions=True)


def main() -> None:
    meminfo = dict(line.split(":") for line in Path("/proc/meminfo").read_text().splitlines())
    figure("host.mem_available_mib.before", int(meminfo["MemAvailable"].split()[0]) // 1024)
    with (
        Session() as session,
        session.container(
            "temporalio/temporal",
            ports={0: 7233},
            command=("server", "start-dev", "--ip", "0.0.0.0", "--headless"),
        ) as container,
    ):
        container.wait_ready(0)
        figure(
            "server.cli", container.exec("temporal", "--version").stdout.strip().replace(" ", "_")
        )
        asyncio.run(spike(f"127.0.0.1:{container.host_ports[0]}"))
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=wdf.dev/verify-run"],
        capture_output=True,
        text=True,
        check=True,
    )
    figure("containers.left.label_all", len(left.stdout.split()))


if __name__ == "__main__":
    main()
