"""Checkpoints against a real store: write, verify, interruption, corruption and an outage."""

import hashlib
import json
import socket
from datetime import UTC, datetime

import pytest
from wdf_artifactclient.checkpoint import (
    DATA_NAME,
    METADATA_NAME,
    prune_plan,
    verify_checkpoint,
    write_checkpoint,
)
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.errors import CheckpointCorrupt, StoreUnavailable
from wdf_artifactclient.keys import split_uri
from wdf_events import build_event, from_bytes, to_bytes
from wdf_events.models import ProducerRef
from wdf_storecheck.s3 import client as s3_client

JOB = "123e4567-e89b-42d3-a456-426614174000"
WASM = hashlib.sha256(b"component").hexdigest()
META = {"format_version": 3, "progress": {"step": 1200, "of": [1, 2]}, "wasm_sha256": WASM}


def producer(attempt: str = "r0a1") -> ProducerRef:
    return ProducerRef(job=JOB, task="sim", attempt=attempt)


def state(size: int = 4096) -> bytes:
    return hashlib.shake_256(f"state {size}".encode()).digest(size)


class Naps:
    def __init__(self):
        self.sleeps: list[float] = []
        self.policy = RetryPolicy(sleep=self.sleeps.append)


@pytest.fixture
def naps():
    return Naps()


@pytest.fixture
def worker(store_session, naps):
    return ArtifactClient(
        store_session.endpoint, store_session.credentials["simulate"], naps.policy
    )


@pytest.fixture
def admin(store_session):
    return s3_client(store_session.endpoint, store_session.credentials["admin"])


@pytest.fixture(autouse=True, scope="module")
def buckets(store_session):
    store_session.store().create_buckets()


@pytest.fixture
def tenant(request):
    return hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]


def write(worker, tenant, data: bytes = b"", meta=META, attempt="r0a1", segment="0", **kwargs):
    return write_checkpoint(
        worker, data or state(), producer(attempt), tenant, "b1", 7, segment, meta, **kwargs
    )


def keys_under(admin, tenant: str) -> list[str]:
    reply = admin.list_objects_v2(Bucket="wdf-checkpoints", Prefix=f"{tenant}/")
    return sorted(item["Key"] for item in reply.get("Contents", []))


pytestmark = pytest.mark.integration


def test_write_then_verify(worker, tenant, admin):
    data = state(5000)
    reference = write(worker, tenant, data)
    assert reference.kind == "checkpoint" and reference.name == METADATA_NAME
    directory = f"{tenant}/b1/{JOB}/7/sim/r0a1/0/"
    assert keys_under(admin, tenant) == [directory + METADATA_NAME, directory + DATA_NAME]

    verified = verify_checkpoint(worker, reference)
    assert verified.reference == reference
    assert [o.sha256 for o in verified.objects] == [hashlib.sha256(data).hexdigest()]
    assert verified.objects[0].uri == f"s3://wdf-checkpoints/{directory}{DATA_NAME}"


def test_metadata_round_trips_wasm_hash_and_opaque_fields(worker, tenant):
    for meta in (
        META,
        {"format_version": "v1", "progress": None, "wasm_sha256": WASM},
        {"format_version": [1, {"a": 2}], "progress": "42%", "wasm_sha256": WASM},
    ):
        verified = verify_checkpoint(worker, write(worker, tenant, meta=meta, segment="s"))
        assert (verified.format_version, verified.progress) == (
            meta["format_version"],
            meta["progress"],
        )
        assert verified.wasm_sha256 == WASM


@pytest.mark.parametrize(
    "meta",
    [
        {"format_version": 1, "progress": 1},
        {**META, "extra": 1},
        {**META, "wasm_sha256": "ABC"},
    ],
)
def test_the_metadata_is_refused_before_anything_is_stored(worker, tenant, admin, meta):
    with pytest.raises(ValueError):
        write(worker, tenant, meta=meta)
    assert keys_under(admin, tenant) == []


def test_interrupted_write_is_invisible(worker, tenant, admin, monkeypatch):
    real = worker.put_attempt

    def fail_on_metadata(source, kind, name, *args, **kwargs):
        if name == METADATA_NAME:
            raise StoreUnavailable("the store went away between the two objects")
        return real(source, kind, name, *args, **kwargs)

    monkeypatch.setattr(worker, "put_attempt", fail_on_metadata)
    with pytest.raises(StoreUnavailable):
        write(worker, tenant)
    keys = keys_under(admin, tenant)
    assert [key.rsplit("/", 1)[1] for key in keys] == [DATA_NAME]


def test_flipped_data_byte_is_checkpoint_corrupt(worker, tenant, admin):
    data = state(9000)
    reference = write(worker, tenant, data)
    data_ref = verify_checkpoint(worker, reference).objects[0]
    bucket, key = split_uri(data_ref.uri)
    flipped = bytearray(data)
    flipped[4000] ^= 1
    admin.put_object(Bucket=bucket, Key=key, Body=bytes(flipped))
    with pytest.raises(CheckpointCorrupt) as corrupt:
        verify_checkpoint(worker, reference)
    assert corrupt.value.reason.startswith("CHECKPOINT_CORRUPT: ")


def test_missing_data_object_is_checkpoint_corrupt(worker, tenant, admin):
    reference = write(worker, tenant)
    data_ref = verify_checkpoint(worker, reference).objects[0]
    bucket, key = split_uri(data_ref.uri)
    admin.delete_object(Bucket=bucket, Key=key)
    with pytest.raises(CheckpointCorrupt):
        verify_checkpoint(worker, reference)


def test_missing_metadata_is_checkpoint_corrupt(worker, tenant, admin):
    reference = write(worker, tenant)
    bucket, key = split_uri(reference.uri)
    admin.delete_object(Bucket=bucket, Key=key)
    with pytest.raises(CheckpointCorrupt):
        verify_checkpoint(worker, reference)


def test_metadata_that_is_not_checkpoint_metadata_is_checkpoint_corrupt(worker, tenant, admin):
    reference = write(worker, tenant)
    bucket, key = split_uri(reference.uri)
    body = json.dumps({"format_version": 1}).encode()
    admin.put_object(Bucket=bucket, Key=key, Body=body)
    forged = reference.model_copy(
        update={"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    )
    with pytest.raises(CheckpointCorrupt, match="not checkpoint metadata"):
        verify_checkpoint(worker, forged)


def test_a_data_object_outside_the_segment_is_checkpoint_corrupt(worker, tenant, admin):
    other = write(worker, tenant, segment="other")
    victim = write(worker, tenant, segment="victim")
    stray = verify_checkpoint(worker, other).objects[0]
    bucket, key = split_uri(victim.uri)
    document = json.loads(admin.get_object(Bucket=bucket, Key=key)["Body"].read())
    document["objects"] = [stray.model_dump(mode="json")]
    body = json.dumps(document).encode()
    admin.put_object(Bucket=bucket, Key=key, Body=body)
    forged = victim.model_copy(
        update={"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    )
    with pytest.raises(CheckpointCorrupt, match="not under the segment"):
        verify_checkpoint(worker, forged)


def test_a_reference_that_is_not_the_metadata_object_is_refused(worker, tenant):
    data_ref = verify_checkpoint(worker, write(worker, tenant)).objects[0]
    with pytest.raises(ValueError, match="not the checkpoint.json"):
        verify_checkpoint(worker, data_ref)


def test_store_outage_during_verify_is_store_unavailable_and_retried(
    worker, tenant, naps, store_session
):
    reference = write(worker, tenant)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    lost = ArtifactClient(
        f"http://127.0.0.1:{closed}", store_session.credentials["simulate"], naps.policy
    )
    with pytest.raises(StoreUnavailable) as raised:
        verify_checkpoint(lost, reference)
    assert raised.value.reason.startswith("STORE_UNAVAILABLE: ")
    assert len(naps.sleeps) == naps.policy.tries - 1
    verify_checkpoint(worker, reference)


def test_prune_plan_keeps_the_newest_and_never_fewer_than_two():
    newest_first = [f"c{n}" for n in range(6, 0, -1)]
    assert prune_plan(newest_first, 4) == ["c2", "c1"]
    assert prune_plan(newest_first, 2) == ["c4", "c3", "c2", "c1"]
    assert prune_plan(newest_first, 1) == ["c4", "c3", "c2", "c1"]
    assert prune_plan(newest_first, 0) == ["c4", "c3", "c2", "c1"]
    assert prune_plan(newest_first, 9) == []
    assert prune_plan(newest_first[:2], 2) == []
    assert prune_plan([], 2) == []


def test_reference_fits_a_heartbeat_detail(worker, tenant):
    reference = write(worker, tenant)
    encoded = json.dumps(reference.model_dump(mode="json"), separators=(",", ":"))
    assert len(encoded.encode()) < 400

    event = build_event(
        "task.checkpoint.committed",
        source="/wdf/workflow",
        tenant=tenant,
        jobid=JOB,
        priorityclass="standard",
        jobclass="simulate",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=datetime(2026, 11, 5, tzinfo=UTC),
        data={"sequence": 0, "checkpoint": json.loads(encoded)},
        taskid="sim",
        attempt=1,
    )
    assert from_bytes(to_bytes(event)).data["checkpoint"] == json.loads(encoded)
