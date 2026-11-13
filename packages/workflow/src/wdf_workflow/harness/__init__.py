"""The durability harness: batches of smoke jobs run through the job workflow, with FakePool workers
on the host, while the harness kills, restarts or replaces what the jobs run on.

A smoke job is a compile, four simulate tasks over it and an aggregate over them, and every second
job reuses one source, so its compile hits the cache once a first one recorded it. The harness
does what admission would: it gives each job a row claimed from `accepted` to `admitted` and
starts its workflow with the start options of the naming contract.

Two tiers run it. `Local` (T1) is a dev server, where the harness runs the build's workflow worker
itself and FakeControl serves the control activities, so nothing is seeded. `Cluster` (T2, in
`wdf_workflow.harness.cluster`) is the deployed workflow worker with the real control activities,
for which the harness seeds rows, manifests and sources.
"""

import asyncio
import hashlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType, IndexedValueType
from temporalio.api.operatorservice.v1 import ListSearchAttributesRequest
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client, WorkflowHandle
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkerDeploymentVersion,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker, WorkerDeploymentConfig
from wdf_artifactclient.keys import content_key, uri_of
from wdf_artifactreaper.schedule import SCHEDULE_ID
from wdf_compile import bundle
from wdf_events import Event, build_event
from wdf_events.models import ContentReference, JobParams
from wdf_jobdoc.plan_models import Plan
from wdf_keys import workflow_id
from wdf_plan import bind, canonical_bytes, manifest_reference, read_plan
from wdf_runtime.models import BuildInfo
from wdf_runtime.pins import COMPONENTIZE_PY_VERSION, WIT_VERSION

from .. import versioning
from ..activities.plan import limits
from ..budgets import INFRA_RETRIES
from ..contract import naming, pools
from ..models import StatusQuery, TaskInput, TaskResult
from ..placement import StaticPlacer, place_activity
from ..service import control_queue
from ..testing import Die, FakeControl, FakePool, Succeed, Zombie
from ..testing.control import Emit, Emitted, LoadedPlan
from ..testing.pool import Outcome

PROFILE = "ci"
TENANT = "harness"
PARAMS = JobParams(seed=7, parameters={})
ENTRY = "sim"
LOCKFILE = b"version = 1\n"
SIMULATE_TASKS = 4
# The slot of each pool in pool table v0, which a task may not exceed.
SIZES = {
    "compile": {"cpu_milli": 1000, "memory_mib": 1024},
    "simulate": {"cpu_milli": 250, "memory_mib": 384},
    "aggregate": {"cpu_milli": 1000, "memory_mib": 1024},
}
# How long a simulate attempt runs when the harness needs tasks in flight, well inside the
# heartbeat timeout, since a fake attempt that holds does not beat.
HOLD_S = 5.0
# A job that has not ended by then counts as failed, so a wedged run still prints its counters.
DEADLINE_S = 3600.0
# How many workflow results are awaited at once, which bounds the long polls on one connection.
WAITERS = 64
# The build of the workflow worker that a T1 run starts, when WDF_BUILD_ID names none.
LOCAL_BUILD = "harness"
WORKFLOW_WORKER = "workflow-worker"
SCENARIOS = ("pause-resume", "zombie", "worker-death")
# What a T1 run refuses: both read the job row or the index, which a T1 run does not have.
ROW_SCENARIOS = ("pause-resume", "zombie")
# The context attribute of the accepted event behind each search attribute of naming.yaml.
SEARCH_ATTRIBUTES = {
    "WdfTenant": "tenant",
    "WdfPriorityClass": "priorityclass",
    "WdfJobClass": "jobclass",
    "WdfJobId": "jobid",
    "WdfSubmittedAt": "time",
}
TYPES = {
    IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD: "keyword",
    IndexedValueType.INDEXED_VALUE_TYPE_DATETIME: "datetime",
}


class Refused(Exception):
    """A command or scenario that this tier cannot run, which the command line exits 2 for."""


def source_bundle(variant: str) -> bytes:
    """A source bundle that wdf_compile accepts, with a lockfile and the entry module, whose bytes
    differ per variant."""
    with tempfile.TemporaryDirectory(prefix="wdf-harness-") as work:
        tree = Path(work) / "tree"
        tree.mkdir()
        (tree / "pyproject.toml").write_text('[project]\nname = "harness"\nversion = "0"\n')
        (tree / "uv.lock").write_bytes(LOCKFILE)
        (tree / f"{ENTRY}.py").write_text(f"VARIANT = {variant!r}\n")
        return bundle.write(tree, Path(work) / "out").read_bytes()


def content(body: bytes, kind: str, name: str) -> ContentReference:
    sha256 = hashlib.sha256(body).hexdigest()
    uri = uri_of(kind, content_key(kind, sha256))
    return ContentReference(uri=uri, sha256=sha256, size=len(body), name=name, kind=kind)


def smoke_plan(source: ContentReference) -> Plan:
    """The plan of a smoke job over a source: a compile, four simulate tasks over its component
    and an aggregate over their results, with no placeholder, so it binds to any params."""
    sims = [f"sim-{n}" for n in range(1, SIMULATE_TASKS + 1)]

    def task(tid: str, kind: str, deps: list[str], output: str | None, params: dict) -> dict:
        return {
            "id": tid,
            "type": kind,
            "pool": kind,
            "depends_on": deps,
            "inputs": [{"task": dep, "output": output} for dep in deps],
            **SIZES[kind],
            "run_timeout_s": 600,
            "retries": 1,
            "checkpoint": False,
            "allow_partial": False,
            "params": params,
        }

    compile_params = {"source": source.model_dump(mode="json"), "entry": ENTRY}
    simulate_params = {"workload": "pi", "args": {"n": 1000}, "seed": PARAMS.seed}
    return Plan.model_validate(
        {
            "plan_version": 1,
            "profile": PROFILE,
            "max_parallel": 8,
            "parameters": {},
            "tasks": [
                task("build", "compile", [], None, compile_params),
                *(task(sim, "simulate", ["build"], "component", simulate_params) for sim in sims),
                task("agg", "aggregate", sims, "result", {"aggregation": "digest_list"}),
            ],
        }
    )


@dataclass(frozen=True)
class Job:
    """One smoke job: its accepted event, its manifest's bytes and its source's bytes."""

    event: Event
    manifest: bytes
    source: bytes

    @property
    def job_id(self) -> str:
        return self.event.jobid

    def loaded(self) -> LoadedPlan:
        """The plan as load_plan returns it, bound the way load_plan binds it."""
        profile = limits().profiles[PROFILE]
        plan = bind(read_plan(self.manifest), PARAMS.model_dump(), profile)
        return LoadedPlan(plan=plan, max_history_events=profile.max_history_events)


def smoke_jobs(count: int) -> list[Job]:
    """`count` smoke jobs, the even ones sharing one source and each odd one with its own."""
    shared = source_bundle("shared")
    jobs = []
    for n in range(count):
        source = shared if n % 2 == 0 else source_bundle(f"job-{n}")
        plan = smoke_plan(content(source, "source", "bundle.tar"))
        event = build_event(
            "job.accepted",
            source="/wdf/dispatch",
            tenant=TENANT,
            jobid=str(uuid.uuid4()),
            priorityclass="standard",
            jobclass="short",
            traceparent=f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01",
            time=datetime.now(UTC),
            data={
                "manifest": manifest_reference(plan),
                "batch_id": str(uuid.uuid4()),
                "submission_key": f"harness-{uuid.uuid4()}",
            },
        )
        jobs.append(Job(event, canonical_bytes(plan), source))
    return jobs


def search_attributes(event: Event) -> TypedSearchAttributes:
    """The search attributes that admission sets at the start, from the accepted event."""
    pairs = []
    for name, kind in naming().search_attributes.items():
        value = getattr(event, SEARCH_ATTRIBUTES[name])
        key = (
            SearchAttributeKey.for_datetime
            if kind == "datetime"
            else SearchAttributeKey.for_keyword
        )
        pairs.append(SearchAttributePair(key(name), value))
    return TypedSearchAttributes(pairs)


class Pool(FakePool):
    """A FakePool whose compile writes a build-info naming the task's cache key, which
    cache_record reads, and whose succeeding attempts take `hold_s`."""

    def __init__(self, *args, hold_s: float = 0.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.hold_s = hold_s
        # The attempts this worker is running now.
        self.running = 0

    async def _run(self, task: TaskInput, outcome: Outcome) -> TaskResult:
        self.running += 1
        try:
            if self.hold_s and isinstance(outcome, Succeed):
                await asyncio.sleep(self.hold_s)
            return await super()._run(task, outcome)
        finally:
            self.running -= 1

    async def _content(self, body: bytes, kind: str, name: str) -> ContentReference:
        if self.store is None:
            return content(body, kind, name)
        return await asyncio.to_thread(self.store.put_content, body, kind, name)

    async def _succeed(self, task: TaskInput, started_at: str) -> TaskResult:
        if task.cache_key is None:
            return await super()._succeed(task, started_at)
        component = await self._content(
            f"component of {task.cache_key}".encode(), "component", "component.wasm"
        )
        info = BuildInfo.model_validate(
            {
                "schema": "wdf.build-info/1",
                "cache_key": task.cache_key,
                "inputs": {
                    "source_hash": task.params.source.sha256,
                    "lockfile_hash": hashlib.sha256(LOCKFILE).hexdigest(),
                    "componentize_py_version": COMPONENTIZE_PY_VERSION,
                    "wit_version": WIT_VERSION,
                    "entry": task.params.entry,
                },
                "tools": {
                    "componentize_py": COMPONENTIZE_PY_VERSION,
                    "wasm_tools": "fake",
                    "uv": "fake",
                    "guest_python": "fake",
                },
                "component": {"sha256": component.sha256, "size": component.size},
                "imports": [],
                "exports": [],
                "timings_s": {"unpack": 0, "install": 0, "componentize": 0, "check": 0},
            }
        )
        build_info = await self._content(
            info.model_dump_json(by_alias=True).encode(), "build-info", "build-info.json"
        )
        return TaskResult(
            outputs=[component, build_info],
            log_tail=await self._log_tail(task),
            started_at=started_at,
        )


Scripts = dict[str, Callable[[TaskInput], Outcome]]


class Rows(FakeControl):
    """FakeControl with one job row per job, each claimed `admitted` when its job is added."""

    def __init__(self):
        super().__init__()
        self.rows: dict[str, str] = {}

    def add(self, job: Job) -> None:
        self.rows[job.job_id] = "admitted"
        self.plans[job.event.data["manifest"]["uri"]] = job.loaded()

    @activity.defn(name="emit")
    async def emit(self, request: Emit) -> Emitted:
        # FakeControl moves its one row, and the call never yields, so no other job's emit runs
        # between the swap in and the swap out.
        self.status = self.rows.get(request.job_id)
        try:
            return await FakeControl.emit(self, request)
        finally:
            if request.job_id in self.rows:
                self.rows[request.job_id] = self.status


class Local:
    """T1: a dev server, on which the harness runs the build of WDF_BUILD_ID, its control queue
    served by FakeControl, and the pools. Only the workflow worker can be restarted."""

    tier = "T1"

    def __init__(self, address: str):
        self.address = address
        os.environ.setdefault(naming().workflow.build_id_env, LOCAL_BUILD)
        self.build_id = os.environ[naming().workflow.build_id_env]
        self.rows = Rows()
        self.client: Client | None = None
        self._workflow: tuple[Worker, asyncio.Task] | None = None

    def refuse(self, command: str, scenario: str | None, targets: list[str]) -> None:
        if scenario in ROW_SCENARIOS:
            raise Refused(
                f"{scenario} reads the job row or the index, which a T1 run does not seed; "
                "run it with --kube-context"
            )
        if command == "rollout":
            raise Refused("rollout builds and deploys an image; run it with --kube-context")
        if other := [t for t in targets if t != WORKFLOW_WORKER]:
            raise Refused(f"a T1 run restarts only {WORKFLOW_WORKER}, not {', '.join(other)}")

    async def __aenter__(self) -> "Local":
        from ..testing import connect

        self.client = await connect(self.address)
        return self

    async def __aexit__(self, *exc) -> None:
        pass

    async def seed(self, jobs: list[Job]) -> None:
        for job in jobs:
            self.rows.add(job)

    def start_options(self, job: Job) -> dict:
        # The dev server knows none of the search attributes that the cluster's bootstrap adds.
        return {}

    def _workflow_worker(self) -> Worker:
        from ..job import JobWorkflow

        return Worker(
            self.client,
            task_queue=naming().workflow.queue,
            workflows=[JobWorkflow],
            activities=[place_activity(StaticPlacer()), self.rows.emit],
            deployment_config=WorkerDeploymentConfig(
                version=WorkerDeploymentVersion(naming().workflow.deployment, self.build_id),
                use_worker_versioning=True,
            ),
        )

    def _start_workflow_worker(self) -> None:
        worker = self._workflow_worker()
        self._workflow = (worker, asyncio.create_task(worker.run()))

    async def _stop_workflow_worker(self) -> None:
        worker, run = self._workflow
        await worker.shutdown()
        await run

    @asynccontextmanager
    async def running(self, scripts: Scripts, hold: dict[str, float]) -> AsyncIterator[dict]:
        async with AsyncExitStack() as stack:
            control = Worker(
                self.client,
                task_queue=control_queue(self.build_id),
                activities=self.rows.control_activities(),
            )
            await stack.enter_async_context(control)
            fakes = {
                pool: Pool(self.client, pool, script, hold_s=hold.get(pool, 0.0))
                for pool, script in scripts.items()
            }
            for fake in fakes.values():
                await stack.enter_async_context(fake)
            self._start_workflow_worker()
            stack.push_async_callback(self._stop_workflow_worker)
            await versioning.set_current(self.client, self.build_id)
            yield fakes

    async def restart(self, targets: list[str]) -> None:
        await self._stop_workflow_worker()
        self._start_workflow_worker()

    async def zombie_commits(self, zombies: list[TaskInput]) -> int:
        raise Refused("zombie reads the index")

    async def pause_resume(self, handle: WorkflowHandle, job: Job) -> bool:
        raise Refused("pause-resume reads the job row")


@dataclass
class Counters:
    finished: int = 0
    failed: int = 0
    infra_retries: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    # Set once the lines are out, as a rollout prints them before it deletes the old build.
    printed: bool = False

    def lines(self) -> list[str]:
        head = [
            f"finished: {self.finished}",
            f"failed: {self.failed}",
            f"infra_retries: {self.infra_retries}",
        ]
        return head + [f"{name}: {value}" for name, value in self.extra.items()]


async def describe(client: Client) -> list[str]:
    """The namespace, its retention, its custom search attributes with their types and the
    reaper's Schedule, or `none` where there is none."""
    name = client.namespace
    described = await client.workflow_service.describe_namespace(
        DescribeNamespaceRequest(namespace=name)
    )
    retention = described.config.workflow_execution_retention_ttl.ToTimedelta()
    lines = [f"namespace: {name}", f"retention: {int(retention.total_seconds()) // 3600}h"]
    listed = await client.operator_service.list_search_attributes(
        ListSearchAttributesRequest(namespace=name)
    )
    wanted = naming().search_attributes
    for attribute, kind in sorted(listed.custom_attributes.items()):
        if attribute in wanted:
            lines.append(f"search_attribute: {attribute} {TYPES.get(kind, kind)}")
    try:
        await client.get_schedule_handle(SCHEDULE_ID).describe()
        lines.append(f"schedule: {SCHEDULE_ID}")
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        lines.append("schedule: none")
    return lines


def _script(scenario: str | None, deaths: int, zombie_s: float) -> Scripts:
    """The pool scripts of a scenario: every attempt succeeds, except the first `deaths`
    simulate attempts under worker-death, which die, and the first attempt of each job's sim-1
    under zombie, which completes after its attempt timed out."""
    succeed: Callable[[TaskInput], Outcome] = lambda task: Succeed()  # noqa: E731
    simulate = succeed
    if scenario == "worker-death":
        left = [deaths]

        def simulate(task: TaskInput) -> Outcome:
            if left[0] > 0:
                left[0] -= 1
                return Die()
            return Succeed()

    elif scenario == "zombie":

        def simulate(task: TaskInput) -> Outcome:
            first = task.request.taskid == "sim-1" and task.task_attempt == 1
            return Zombie(after_s=zombie_s) if first else Succeed()

    return {"compile": succeed, "simulate": simulate, "aggregate": succeed}


async def _until(check: Callable[[], Any], timeout_s: float, what: str, every_s: float = 0.5):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not (value := await check() if asyncio.iscoroutinefunction(check) else check()):
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(f"no {what} within {timeout_s:.0f} s")
        await asyncio.sleep(every_s)
    return value


async def start(client: Client, tier, job: Job) -> WorkflowHandle:
    return await client.start_workflow(
        naming().workflow.type,
        args=[job.event, PARAMS],
        id=workflow_id(job.job_id),
        task_queue=naming().workflow.queue,
        id_reuse_policy=WorkflowIDReusePolicy[naming().workflow.id_reuse_policy],
        result_type=str,
        **tier.start_options(job),
    )


async def start_all(client: Client, tier, jobs: list[Job]) -> list[WorkflowHandle]:
    gate = asyncio.Semaphore(WAITERS)

    async def one(job: Job) -> WorkflowHandle:
        async with gate:
            return await start(client, tier, job)

    return await asyncio.gather(*(one(job) for job in jobs))


async def ended(handles: list[WorkflowHandle], deadline_s: float = DEADLINE_S) -> list[str]:
    """The end status of each job, `wedged` for one that has not ended by the deadline and
    `error` for a workflow that failed."""
    gate = asyncio.Semaphore(WAITERS)

    async def one(handle: WorkflowHandle) -> str:
        async with gate:
            try:
                return await asyncio.wait_for(handle.result(), deadline_s)
            except TimeoutError:
                return "wedged"
            except Exception:  # noqa: BLE001 - any failed workflow counts as failed
                return "error"

    return await asyncio.gather(*(one(handle) for handle in handles))


async def infra_retries(client: Client, fakes: dict[str, FakePool]) -> int:
    """The infrastructure budget the jobs spent, read from the status query of each job in which
    a pool saw a task run more than once, since only those can have spent any."""
    retried = {
        task.request.jobid
        for fake in fakes.values()
        for task in fake.calls
        if task.task_attempt > 1
    }
    total = 0
    for job_id in sorted(retried):
        query: StatusQuery = await client.get_workflow_handle(workflow_id(job_id)).query(
            naming().queries[0], result_type=StatusQuery
        )
        total += sum(INFRA_RETRIES - task.infra_left for task in query.tasks)
    return total


async def built_on(handle: WorkflowHandle) -> str:
    """The build of the last workflow task completed in the job's history."""
    build = ""
    async for event in handle.fetch_history_events():
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED:
            build = event.workflow_task_completed_event_attributes.deployment_version.build_id
    return build


def _paused_supported() -> bool:
    from ..job import JobWorkflow

    signals = workflow._Definition.from_class(JobWorkflow).signals
    return {"pause", "resume"} <= set(signals)


async def run_jobs(
    tier, command: str, count: int, scenario: str | None, deaths: int, targets: list[str]
) -> Counters:
    """Runs `count` smoke jobs to their end under the scenario, restarting `targets` once tasks
    are in flight for `restart`, and counts how they ended."""
    if scenario == "pause-resume" and not _paused_supported():
        raise Refused(
            "pause-resume is planned: the job workflow has no pause and resume signals yet"
        )
    client = tier.client
    zombie_s = pools().pools["simulate"].heartbeat_s * 1.25
    restart = command == "restart"
    hold = {"simulate": HOLD_S} if restart or scenario == "pause-resume" else {}
    jobs = smoke_jobs(count)
    await tier.seed(jobs)
    counters = Counters()
    async with tier.running(_script(scenario, deaths, zombie_s), hold) as fakes:
        handles = await start_all(client, tier, jobs)
        drivers = []
        if scenario == "pause-resume":
            drivers = [
                asyncio.create_task(tier.pause_resume(h, j))
                for h, j in zip(handles, jobs, strict=True)
            ]
        if restart:
            await _until(lambda: fakes["simulate"].running, DEADLINE_S, "simulate task in flight")
            counters.extra["in_flight"] = fakes["simulate"].running
            await tier.restart(targets)
            counters.extra["restarted"] = ",".join(targets)
        statuses = await ended(handles)
        counters.finished = statuses.count("succeeded")
        counters.failed = len(statuses) - counters.finished
        if drivers:
            counters.extra["paused_resumed"] = sum(await asyncio.gather(*drivers))
        if scenario == "zombie":
            await _until(
                lambda: len(fakes["simulate"].zombies) >= count, zombie_s * 4, "zombie completion"
            )
            counters.extra["zombie_commits"] = await tier.zombie_commits(fakes["simulate"].zombies)
        counters.infra_retries = await infra_retries(client, fakes)
    return counters


async def _simulating(client: Client, handle: WorkflowHandle) -> bool:
    """Whether the job has a simulate task scheduled, which a job past its compile has."""
    try:
        status = await handle.query(naming().queries[0], result_type=StatusQuery)
    except RPCError:
        return False
    return any(t.state == "running" and t.id.startswith("sim-") for t in status.tasks)


async def rollout(tier, count: int, new: str) -> Counters:
    """Runs `count` jobs on the current build until their simulate tasks wait, deploys build
    `new` beside it and makes it current, runs `count` more jobs, then lets every job finish and
    deletes the old build's Deployment once no open job is pinned to it."""
    client = tier.client
    old = await versioning.current(client)
    if old is None or old == new:
        raise Refused(f"rollout needs a current build other than {new}, and the current is {old}")
    opened, later = smoke_jobs(count), smoke_jobs(count)
    await tier.seed(opened + later)
    scripts = _script(None, 0, 0)
    counters = Counters()
    serving = {pool: script for pool, script in scripts.items() if pool != "simulate"}
    async with tier.running(serving, {}) as fakes:
        open_handles = await start_all(client, tier, opened)
        for handle in open_handles:
            await _until(lambda h=handle: _simulating(client, h), DEADLINE_S, "simulate task")
        await tier.deploy(old, new)
        await versioning.set_current(client, new)
        later_handles = await start_all(client, tier, later)
        async with tier.running({"simulate": scripts["simulate"]}, {}) as simulate:
            statuses = await ended(open_handles + later_handles)
            counters.finished = statuses.count("succeeded")
            counters.failed = len(statuses) - counters.finished
            counters.infra_retries = await infra_retries(client, {**fakes, **simulate})
    builds = [{await built_on(h) for h in handles} for handles in (open_handles, later_handles)]
    counters.extra.update(
        old_build=old,
        new_build=new,
        open_job_build=",".join(sorted(builds[0])),
        new_job_build=",".join(sorted(builds[1])),
    )

    async def drained():
        return (await versioning.drainage(client, old)).drained

    # Visibility follows a job's close by up to a few seconds.
    await _until(drained, 300, f"drainage of {old}", every_s=2)
    counters.extra["drained"] = old
    for line in counters.lines():
        print(line, flush=True)
    counters.printed = True
    await versioning.retire(client, old, tier.deleter())
    print(f"deleted: {versioning.KUBE_NAMESPACE}/workflow-worker-{old}", flush=True)
    return counters
