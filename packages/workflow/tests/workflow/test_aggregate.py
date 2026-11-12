"""Aggregate semantics on a dev server: an aggregate waits for every input, or with
`allow_partial` runs over the inputs that succeeded and ends the job `partial`, reads only the one
index that `build_index` put, or the submitter's own index in an aggregate-only job."""

import asyncio
import json

import pytest
from temporalio import activity
from wdf_events.models import ContentReference
from wdf_jobdoc.plan_models import Plan
from wdf_stages.recording import type_name
from wdf_workflow.contract import check_task_input
from wdf_workflow.job import JobWorkflow
from wdf_workflow.testing import Fail, FakeControl, Succeed, Zombie, connect
from wdf_workflow.testing.control import BuildIndex

pytestmark = pytest.mark.integration

# A job of these tests ends within seconds, so a wedged workflow fails its test.
LIMIT_S = 120
INDEX_HASH = "b" * 64
# An index a submitter put and names in the aggregate's params.
INDEX = {
    "uri": f"s3://wdf-sources/sha256/bb/{INDEX_HASH}",
    "sha256": INDEX_HASH,
    "size": 512,
    "name": "index.jsonl",
    "kind": "index",
}
SIMS = ["sim-a", "sim-b", "sim-c", "sim-d"]


class IndexControl(FakeControl):
    """FakeControl that keeps each `build_index` request and the index it returned."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.indexes: list[tuple[BuildIndex, ContentReference]] = []

    @activity.defn(name="build_index")
    async def build_index(self, request: BuildIndex) -> ContentReference:
        reference = await FakeControl.build_index(self, request)
        self.indexes.append((request, reference))
        return reference


def entries(request: BuildIndex) -> list[tuple[str, str]]:
    return [(entry.task, entry.status) for entry in request.entries]


def with_source(plan: Plan, task_id: str, sha256: str) -> Plan:
    """The plan with another source on one compile task, so its cache key is its own."""
    source = {
        "uri": f"s3://wdf-sources/sha256/{sha256[:2]}/{sha256}",
        "sha256": sha256,
        "size": 2048,
        "name": "bundle.tar.gz",
        "kind": "source",
    }
    document = plan.model_dump(mode="json")
    for task in document["tasks"]:
        if task["id"] == task_id:
            task["params"]["source"] = source
    return Plan.model_validate(document)


def test_aggregate_waits_for_all_inputs(temporal, rig, plan, fan, accepted):
    """The aggregate runs only once every input succeeded, a slow one included, and never once an
    input failed, while the other inputs still run to their end."""
    control = IndexControl()
    # The inputs committed when each aggregate task was taken, by job.
    seen: dict[str, set[str]] = {}
    failing = accepted()

    def aggregate(task):
        jobid = task.request.jobid
        seen[jobid] = {t for j, t in control.committed if j == jobid and t.startswith("sim-")}
        return Succeed()

    def simulate(task):
        if task.request.taskid == "sim-a":
            return Zombie(after_s=2)
        if (task.request.jobid, task.request.taskid) == (failing.jobid, "sim-d"):
            return Fail("guest_memory_cap")
        return Succeed()

    async def body():
        client = await connect(temporal)
        scripts = {"simulate": simulate, "aggregate": aggregate}
        async with rig(client, scripts, control=control) as this:
            whole = await this.start(plan(fan))
            whole_status = await whole.result()
            broken = await this.start(plan(fan), failing)
            broken_status = await broken.result()
            return this, whole_status, broken_status, await broken.query(JobWorkflow.status)

    this, whole_status, broken_status, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert whole_status == "succeeded"
    [(request, _)] = control.indexes
    assert (request.partial, entries(request)) == (False, [(s, "succeeded") for s in SIMS])
    [(jobid, inputs)] = seen.items()
    assert inputs == set(SIMS)
    assert broken_status == "failed"
    assert failing.jobid not in seen
    assert [r.job_id for r, _ in control.indexes] == [jobid]
    states = {t.id: t.state for t in query.tasks}
    assert states == {
        "agg": "blocked",
        # The first job recorded the component, so the second one's compile is a cache hit.
        "build": "skipped",
        "sim-a": "succeeded",
        "sim-b": "succeeded",
        "sim-c": "succeeded",
        "sim-d": "failed",
    }
    assert [t.task_attempt for t in query.tasks if t.id == "agg"] == [0]


def test_allow_partial_partial_status(temporal, rig, plan, fan):
    """With `allow_partial` the aggregate runs over the inputs that succeeded once the others
    ended, its index lists a failed input as failed and a blocked one as missing, and the job ends
    `partial` with every task that did not run to completion in `job.partial`."""
    control = IndexControl()
    tasks = {**fan, "build-x": ("compile", []), "sim-e": ("simulate", ["build-x"])}
    tasks["agg"] = ("aggregate", [*SIMS, "sim-e"])
    scripts = {
        "compile": lambda t: Fail("compile_failed") if t.request.taskid == "build-x" else Succeed(),
        "simulate": lambda t: (
            Fail("guest_memory_cap") if t.request.taskid == "sim-d" else Succeed()
        ),
    }

    async def body():
        client = await connect(temporal)
        async with rig(client, scripts, control=control) as this:
            job = with_source(plan(tasks, allow_partial=frozenset({"agg"})), "build-x", "c" * 64)
            handle = await this.start(job)
            return this, await handle.result(), await handle.query(JobWorkflow.status)

    this, status, query = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "partial"
    assert control.status == "partial"
    [(request, index)] = control.indexes
    assert request.partial is True
    assert entries(request) == [
        ("sim-a", "succeeded"),
        ("sim-b", "succeeded"),
        ("sim-c", "succeeded"),
        ("sim-d", "failed"),
        ("sim-e", "missing"),
    ]
    for entry in request.entries[:3]:
        [result, _] = control.committed[(request.job_id, entry.task)]
        assert entry.reference == result
    assert [e.reference for e in request.entries[3:]] == [None, None]
    [aggregate] = this.pools["aggregate"].calls
    assert aggregate.request.data["inputs"] == [index.model_dump(mode="json")]
    [end] = [e for e in control.events.values() if type_name(e.type) == "job.partial"]
    assert end.data == {"skipped_tasks": ["build-x", "sim-d", "sim-e"]}
    assert query.job_status == "partial"
    assert {t.id: t.state for t in query.tasks} == {
        "agg": "succeeded",
        "build": "succeeded",
        "build-x": "failed",
        "sim-a": "succeeded",
        "sim-b": "succeeded",
        "sim-c": "succeeded",
        "sim-d": "failed",
        "sim-e": "blocked",
    }


def test_aggregate_only_job(temporal, rig, plan):
    """An aggregate with no dependencies reads the index its params name, and no index is built."""
    control = IndexControl()

    async def body():
        client = await connect(temporal)
        async with rig(client, control=control) as this:
            job = plan(
                {"agg": ("aggregate", [])}, aggregate={"aggregation": "digest_list", "index": INDEX}
            )
            handle = await this.start(job)
            return this, await handle.result()

    this, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    assert "build_index" not in control.calls
    [aggregate] = this.pools["aggregate"].calls
    assert aggregate.request.data["inputs"] == [INDEX]
    check_task_input(aggregate)
    [summary, _] = control.committed[(aggregate.request.jobid, "agg")]
    assert summary.kind == "summary"


def test_aggregate_gets_index_reference_only(temporal, rig, plan, fan):
    """The aggregate's task input holds the one index over the results in task id order, and no
    result reference itself."""
    control = IndexControl()

    async def body():
        client = await connect(temporal)
        async with rig(client, control=control) as this:
            handle = await this.start(plan(fan))
            return this, await handle.result()

    this, status = asyncio.run(asyncio.wait_for(body(), LIMIT_S))
    assert status == "succeeded"
    [(request, index)] = control.indexes
    [aggregate] = this.pools["aggregate"].calls
    check_task_input(aggregate)
    assert aggregate.request.data["inputs"] == [index.model_dump(mode="json")]
    assert aggregate.params.index is None
    assert [e.task for e in request.entries] == SIMS
    payload = json.dumps(aggregate.model_dump(mode="json"))
    for entry in request.entries:
        assert entry.reference == control.committed[(request.job_id, entry.task)][0]
        assert entry.reference.uri not in payload
