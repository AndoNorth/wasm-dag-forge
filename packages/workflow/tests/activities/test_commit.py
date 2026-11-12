"""The commit control activity against a real store and database, as a plain function under a
`wdf_artifacts` login with the `workflow` store identity."""

import asyncio
import uuid

import pytest
from temporalio.exceptions import ApplicationError
from wdf_events.models import ProducerRef
from wdf_workflow.activities import Committer
from wdf_workflow.testing.control import Commit, ControlCase, control_activity_contract

pytestmark = [pytest.mark.integration, pytest.mark.pair]

TENANT = "acme"


@pytest.fixture
def committer(login, workflow_store):
    return Committer(login("wdf_artifacts"), workflow_store)


@pytest.fixture
def outputs(role, batch):
    """The result and log tail that a simulate attempt of a job wrote, with a component of the
    job's compile that every job may link."""

    def write(job_id: str, attempt: str, task: str = "sim-1"):
        simulate = role("simulate")
        who = ProducerRef(job=job_id, task=task, attempt=attempt)
        result = simulate.put_attempt(
            f"result of {attempt}".encode(), "result", "results.xml", who, TENANT, batch, 7
        )
        tail = simulate.put_attempt(b"tail", "log-tail", "log-tail.txt", who, TENANT, batch, 7)
        component = role("compile").put_content(
            f"component of {job_id}".encode(), "component", "component.wasm"
        )
        return [result, tail, component]

    return write


def refused(committer, request) -> ApplicationError:
    with pytest.raises(ApplicationError) as raised:
        committer.commit(request)
    return raised.value


def test_commit_indexes_accepted_attempt(committer, outputs, rows, uses):
    job_id = str(uuid.uuid4())
    result, tail, component = outputs(job_id, "r0a2")
    committer.commit(Commit(job_id=job_id, task="sim-1", attempt="r0a2", references=[result, tail]))
    assert rows([result.uri, tail.uri]) == sorted([(result.uri, "r0a2"), (tail.uri, "r0a2")])
    assert uses(job_id) == []

    # A content-addressed output, as compile writes, is linked to the job that committed it.
    committer.commit(Commit(job_id=job_id, task="build", attempt="r0a1", references=[component]))
    assert rows([component.uri]) == [(component.uri, None)]
    assert uses(job_id) == [component.uri]

    # A retry of the same commit changes nothing.
    committer.commit(Commit(job_id=job_id, task="sim-1", attempt="r0a2", references=[result, tail]))
    assert len(rows([result.uri, tail.uri, component.uri])) == 3


def test_commit_refuses_zombie_attempt(committer, outputs, rows):
    job_id = str(uuid.uuid4())
    zombie = outputs(job_id, "r0a1")
    accepted = outputs(job_id, "r0a2")
    error = refused(
        committer,
        Commit(job_id=job_id, task="sim-1", attempt="r0a2", references=[accepted[0], zombie[0]]),
    )
    assert (error.type, error.non_retryable) == ("CommitRefused", True)
    assert "r0a1" in str(error)
    assert rows([zombie[0].uri, accepted[0].uri]) == []

    # An attempt of another job or another task is refused the same way.
    other = outputs(str(uuid.uuid4()), "r0a2")
    for request in (
        Commit(job_id=job_id, task="sim-1", attempt="r0a2", references=[other[0]]),
        Commit(job_id=job_id, task="sim-2", attempt="r0a2", references=[accepted[0]]),
    ):
        assert refused(committer, request).type == "CommitRefused"
    assert rows([other[0].uri, accepted[0].uri]) == []


def test_commit_upload_failed_reaches_workflow(committer, outputs, vanish, rows, uses):
    job_id = str(uuid.uuid4())
    result, tail, component = outputs(job_id, "r0a1")
    vanish(tail)
    error = refused(
        committer, Commit(job_id=job_id, task="sim-1", attempt="r0a1", references=[result, tail])
    )
    # Retryable, so it reaches the workflow as the class that runs the attempt again.
    assert (error.type, error.non_retryable) == ("OUTPUT_UPLOAD_FAILED", False)
    assert rows([result.uri, tail.uri]) == []

    short = component.model_copy(update={"size": component.size + 1})
    error = refused(
        committer, Commit(job_id=job_id, task="build", attempt="r0a1", references=[short])
    )
    assert (error.type, error.non_retryable) == ("OUTPUT_UPLOAD_FAILED", False)
    assert rows([component.uri]) == []
    assert uses(job_id) == []


def test_commit_passes_control_contract(committer, outputs, vanish):
    job_id = str(uuid.uuid4())
    zombie = outputs(job_id, "r0a1")
    accepted = outputs(job_id, "r0a2")
    lost = vanish(outputs(job_id, "r0a2", task="sim-2")[0])
    cases = [
        ControlCase(
            "commit",
            Commit(job_id=job_id, task="sim-1", attempt="r0a2", references=accepted[:2]),
        ),
        ControlCase(
            "commit",
            Commit(job_id=job_id, task="sim-1", attempt="r0a2", references=zombie[:1]),
            raises="CommitRefused",
        ),
        ControlCase(
            "commit",
            Commit(job_id=job_id, task="sim-2", attempt="r0a2", references=[lost]),
            raises="OUTPUT_UPLOAD_FAILED",
        ),
    ]
    asyncio.run(control_activity_contract({"commit": committer.commit}, cases))
