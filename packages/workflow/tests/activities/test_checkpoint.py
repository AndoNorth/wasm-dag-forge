"""The index_checkpoint control activity against a real database, as a plain function under a
`wdf_artifacts` login, with checkpoints that a simulate worker wrote to a real store."""

import asyncio
import uuid

import pytest
from temporalio.exceptions import ApplicationError
from wdf_artifactclient.checkpoint import write_checkpoint
from wdf_events.models import ProducerRef
from wdf_workflow.activities import Committer
from wdf_workflow.testing.control import ControlCase, IndexCheckpoint, control_activity_contract

pytestmark = [pytest.mark.integration, pytest.mark.pair]

META = {"format_version": 1, "progress": 0.5, "wasm_sha256": "e" * 64}


@pytest.fixture
def committer(login):
    """A committer with no store client, since indexing a checkpoint never reads the store."""
    return Committer(login("wdf_artifacts"), None)


@pytest.fixture
def checkpoint(role, batch):
    def write(job_id: str, attempt: str, task: str = "sim-1"):
        who = ProducerRef(job=job_id, task=task, attempt=attempt)
        body = f"state of {task} {attempt}".encode()
        return write_checkpoint(role("simulate"), body, who, "acme", batch, 7, "1", META)

    return write


def test_index_checkpoint_indexes_reference(committer, checkpoint, rows):
    job_id = str(uuid.uuid4())
    reference = checkpoint(job_id, "r1a2")
    request = IndexCheckpoint(reference=reference, job_id=job_id, task="sim-1", attempt="r1a2")
    committer.index_checkpoint(request)
    assert rows([reference.uri]) == [(reference.uri, "r1a2")]
    committer.index_checkpoint(request)
    assert rows([reference.uri]) == [(reference.uri, "r1a2")]


def test_index_checkpoint_refuses_other_attempt(committer, checkpoint, rows):
    job_id = str(uuid.uuid4())
    zombie = checkpoint(job_id, "r0a1")
    other_task = checkpoint(job_id, "r0a2", task="sim-2")
    other_job = checkpoint(str(uuid.uuid4()), "r0a2")
    for reference in (zombie, other_task, other_job):
        request = IndexCheckpoint(reference=reference, job_id=job_id, task="sim-1", attempt="r0a2")
        with pytest.raises(ApplicationError) as raised:
            committer.index_checkpoint(request)
        assert (raised.value.type, raised.value.non_retryable) == ("CommitRefused", True)
    assert rows([zombie.uri, other_task.uri, other_job.uri]) == []


def test_index_checkpoint_passes_control_contract(committer, checkpoint):
    job_id = str(uuid.uuid4())
    reference = checkpoint(job_id, "r0a1")
    cases = [
        ControlCase(
            "index_checkpoint",
            IndexCheckpoint(reference=reference, job_id=job_id, task="sim-1", attempt="r0a1"),
        ),
        ControlCase(
            "index_checkpoint",
            IndexCheckpoint(reference=reference, job_id=job_id, task="sim-1", attempt="r0a2"),
            raises="CommitRefused",
        ),
    ]
    asyncio.run(control_activity_contract({"index_checkpoint": committer.index_checkpoint}, cases))
