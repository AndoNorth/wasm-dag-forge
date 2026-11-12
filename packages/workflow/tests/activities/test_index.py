"""The build_index control activity against a real store and database, as a plain function under a
`wdf_artifacts` login with the `indexer` and `workflow` store identities."""

import asyncio
import uuid

import pytest
from temporalio.exceptions import ApplicationError
from wdf_artifactclient.aggregate import read_index
from wdf_artifactclient.models import IndexEntry
from wdf_events.models import ProducerRef
from wdf_workflow.activities import IndexBuilder
from wdf_workflow.testing.control import BuildIndex, ControlCase, control_activity_contract

pytestmark = [pytest.mark.integration, pytest.mark.pair]

TENANT = "acme"
# Ascending by task id, which is not the order of the numbers in them.
TASKS = ["sim-1", "sim-10", "sim-2", "sim-3"]


@pytest.fixture
def builder(login, workflow_store, role):
    return IndexBuilder(login("wdf_artifacts"), workflow_store, role("indexer"))


@pytest.fixture
def results(role, batch):
    """The recorded result of each task of a job, as the workflow holds them, in the given order."""

    def write(job_id: str, tasks: list[str], failed: tuple[str, ...] = ()) -> list[IndexEntry]:
        simulate = role("simulate")
        entries = []
        for task in tasks:
            if task in failed:
                entries.append(IndexEntry(task=task, status="failed"))
                continue
            who = ProducerRef(job=job_id, task=task, attempt="r0a1")
            reference = simulate.put_attempt(
                f"result of {task}".encode(), "result", "results.xml", who, TENANT, batch, 7
            )
            entries.append(IndexEntry(task=task, status="succeeded", reference=reference))
        return entries

    return write


def request(job_id: str, entries: list[IndexEntry], partial: bool = False) -> BuildIndex:
    return BuildIndex(job_id=job_id, tenant=TENANT, task="agg", partial=partial, entries=entries)


# The ids name the activity, so the selection `-k build_index` takes this test as well.
@pytest.mark.parametrize("partial", [False, True], ids=["build_index-refuse", "build_index-allow"])
def test_index_in_task_id_order(builder, results, role, partial):
    job_id = str(uuid.uuid4())
    failed = ("sim-10",) if partial else ()
    shuffled = results(job_id, ["sim-3", "sim-10", "sim-1", "sim-2"], failed=failed)
    reference = builder.build_index(request(job_id, shuffled, partial=partial))
    read = list(read_index(role("aggregate"), reference, TENANT))
    assert [entry.task for entry in read] == TASKS
    assert read == sorted(shuffled, key=lambda entry: entry.task)
    assert [entry.status for entry in read] == [
        "failed" if task in failed else "succeeded" for task in TASKS
    ]

    # Any order of the same results gives the same object.
    again = builder.build_index(request(job_id, list(reversed(shuffled)), partial=partial))
    assert again == reference


def test_build_index_commits_reference(builder, results, rows, uses):
    job_id = str(uuid.uuid4())
    reference = builder.build_index(request(job_id, results(job_id, TASKS)))
    assert reference.kind == "index"
    assert rows([reference.uri]) == [(reference.uri, None)]
    assert uses(job_id) == [reference.uri]

    # A retry after the commit puts the same object and commits it again.
    assert builder.build_index(request(job_id, results(job_id, TASKS))) == reference
    assert rows([reference.uri]) == [(reference.uri, None)]


def test_build_index_refuses_what_the_aggregate_cannot_read(builder, results):
    job_id = str(uuid.uuid4())
    entries = results(job_id, TASKS, failed=("sim-2",))
    foreign = results(str(uuid.uuid4()), ["sim-3"])
    renamed = IndexEntry(task="sim-4", status="succeeded", reference=entries[0].reference)
    for refused in (
        request(job_id, entries),
        request(job_id, [*entries[:2], *foreign], partial=True),
        request(job_id, [*entries, renamed], partial=True),
        request(job_id, [*entries, entries[0]], partial=True),
    ):
        with pytest.raises(ApplicationError) as raised:
            builder.build_index(refused)
        assert (raised.value.type, raised.value.non_retryable) == ("CommitRefused", True)


def test_build_index_store_fault_is_typed(login, dead_store, role, results):
    job_id = str(uuid.uuid4())
    entries = results(job_id, TASKS)
    for builder in (
        IndexBuilder(login("wdf_artifacts"), role("workflow"), dead_store),
        IndexBuilder(login("wdf_artifacts"), dead_store, role("indexer")),
    ):
        with pytest.raises(ApplicationError) as raised:
            builder.build_index(request(job_id, entries))
        assert (raised.value.type, raised.value.non_retryable) == ("STORE_UNAVAILABLE", False)


def test_build_index_passes_control_contract(builder, results):
    job_id = str(uuid.uuid4())
    cases = [ControlCase("build_index", request(job_id, results(job_id, TASKS)))]
    asyncio.run(control_activity_contract({"build_index": builder.build_index}, cases))
