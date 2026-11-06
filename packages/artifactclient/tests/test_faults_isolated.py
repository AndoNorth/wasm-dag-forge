"""The failure scenarios that damage a store of their own on purpose, except the full store.

The scenarios share one container, so they run in file order and the last one stops it.
A full store has its own module, because the volume that fills holds only one bucket.
"""

import hashlib
import subprocess
import time

import pytest
from wdf_artifactclient import faults, staging
from wdf_artifactclient.checkpoint import verify_checkpoint, write_checkpoint
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.errors import (
    ArtifactCorrupt,
    ArtifactLost,
    ArtifactNotFound,
    CheckpointCorrupt,
    OutputMissing,
    StoreForbidden,
    StoreUnavailable,
)
from wdf_events.models import ProducerRef
from wdf_storecheck.fixtures import running
from wdf_storecheck.s3 import Creds, dump_credentials

pytestmark = [pytest.mark.integration, pytest.mark.isolated]

JOB = "123e4567-e89b-42d3-a456-426614174000"
LIMIT = 1_000_000
DEADLINE = 10.0
META = {
    "format_version": 1,
    "progress": {"step": 1},
    "wasm_sha256": hashlib.sha256(b"c").hexdigest(),
}


def producer(attempt: str = "r0a1") -> ProducerRef:
    return ProducerRef(job=JOB, task="t0", attempt=attempt)


@pytest.fixture(scope="module")
def seaweed(t1, tmp_path_factory):
    with running(t1, "seaweedfs", tmp_path_factory.mktemp("store")) as started:
        started.store().create_buckets()
        yield started


@pytest.fixture(scope="module")
def kept(seaweed):
    """An object that the last scenario reads after the store is gone."""
    client = ArtifactClient(seaweed.endpoint, seaweed.credentials["aggregate"])
    return put_result(client, "kept.bin", b"reachable")


@pytest.fixture(scope="module")
def credentials_file(seaweed, tmp_path_factory):
    path = tmp_path_factory.mktemp("credentials") / "credentials.json"
    dump_credentials(path, seaweed.credentials)
    return path


@pytest.fixture
def role(seaweed):
    """A client of a role, with a policy that does not sleep."""
    return lambda name: ArtifactClient(
        seaweed.endpoint, seaweed.credentials[name], RetryPolicy(sleep=lambda _: None)
    )


@pytest.fixture
def hook(seaweed, credentials_file):
    def run(name: str, as_role: str, uri: str) -> None:
        argv = [name, "--endpoint", seaweed.endpoint, "--credentials", str(credentials_file)]
        assert faults.main([*argv, "--role", as_role, "--uri", uri]) == 0

    return run


def put_result(client, name: str, data: bytes):
    return client.put_attempt(data, "result", name, producer(), "acme", "b1", 1)


def test_a_flipped_byte_gives_corrupt_then_lost(role, hook):
    reference = put_result(role("simulate"), "flipped.bin", b"intact" * 500)
    hook("flip-byte", "aggregate", reference.uri)
    with pytest.raises(ArtifactLost) as lost:
        role("aggregate").get_bytes(reference, LIMIT)
    assert isinstance(lost.value.cause, ArtifactCorrupt)
    assert lost.value.reason.startswith("ARTIFACT_LOST: ")
    assert lost.value.cause.reason.startswith("ARTIFACT_CORRUPT: ")


def test_a_deleted_object_gives_not_found_then_lost(role, hook):
    reference = put_result(role("simulate"), "deleted.bin", b"gone" * 500)
    hook("delete", "reaper", reference.uri)
    with pytest.raises(ArtifactLost) as lost:
        role("aggregate").get_bytes(reference, LIMIT)
    assert isinstance(lost.value.cause, ArtifactNotFound)
    assert lost.value.cause.reason.startswith("ARTIFACT_NOT_FOUND: ")


def test_a_corrupted_checkpoint_fails_and_the_previous_one_verifies(role, hook):
    worker = role("simulate")
    previous, newest = (
        write_checkpoint(worker, bytes([n]) * 4096, producer(), "acme", "b1", 7, str(n), META)
        for n in (0, 1)
    )
    hook("corrupt-checkpoint", "simulate", newest.uri)
    with pytest.raises(CheckpointCorrupt) as corrupt:
        verify_checkpoint(worker, newest)
    assert corrupt.value.reason.startswith("CHECKPOINT_CORRUPT: ")
    assert verify_checkpoint(worker, previous).reference == previous


def test_wrong_credentials_give_store_forbidden(seaweed, role):
    reference = put_result(role("simulate"), "private.bin", b"private")
    stranger = ArtifactClient(seaweed.endpoint, Creds("unknown-key", "unknown-secret"))
    with pytest.raises(StoreForbidden) as refused:
        stranger.get_bytes(reference, LIMIT)
    assert refused.value.reason.startswith("STORE_FORBIDDEN: ")


def test_a_missing_declared_output_gives_output_missing(role, scratch):
    if "collect_outputs" in staging.STUBBED:
        pytest.skip("collect_outputs is not delivered yet")
    with pytest.raises(OutputMissing) as missing:
        staging.collect_outputs(role("simulate"), scratch, "simulate", producer(), "acme", "b1", 1)
    assert missing.value.reason.startswith("OUTPUT_MISSING: ")


def test_a_stopped_store_gives_store_unavailable_within_the_deadline(seaweed, kept):
    client = ArtifactClient(seaweed.endpoint, seaweed.credentials["aggregate"])
    subprocess.run(["docker", "stop", seaweed.container_id], capture_output=True, check=True)
    began = time.monotonic()
    with pytest.raises(StoreUnavailable) as down:
        client.get_bytes(kept, LIMIT, deadline=DEADLINE)
    assert time.monotonic() - began <= DEADLINE
    assert down.value.reason.startswith("STORE_UNAVAILABLE: ")
