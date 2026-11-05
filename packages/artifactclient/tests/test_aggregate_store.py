"""The aggregate index through the client against both object store products."""

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from wdf_artifactclient.aggregate import open_results, put_index, read_index
from wdf_artifactclient.bench import ListCounter
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.errors import ArtifactLost
from wdf_artifactclient.models import IndexEntry
from wdf_events.models import ProducerRef
from wdf_storecheck.s3 import client as s3_client

pytestmark = pytest.mark.integration

JOB = "123e4567-e89b-42d3-a456-426614174000"
TENANT = "acme"
HEADER = {"job": JOB, "tenant": TENANT, "task": "aggregate-0", "partial": "refuse"}


@pytest.fixture(scope="module", autouse=True)
def buckets(store):
    store.store().create_buckets()


@pytest.fixture
def role(store):
    """A client of a role of the contract, with no real waiting between tries."""
    policy = RetryPolicy(sleep=lambda seconds: None)
    return lambda name: ArtifactClient(store.endpoint, store.credentials[name], policy)


@pytest.fixture
def batch(request):
    """A batch name that no other test of the module shares, so that keys never collide."""
    return hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]


def put_results(role, batch: str, count: int) -> list[IndexEntry]:
    simulate = role("simulate")

    def put(i: int) -> IndexEntry:
        task = f"simulate-{i:04d}"
        who = ProducerRef(job=JOB, task=task, attempt="r0a1")
        data = f"result {i}".encode()
        reference = simulate.put_attempt(data, "result", "result.xml", who, TENANT, batch, i)
        return IndexEntry(task=task, status="succeeded", reference=reference)

    with ThreadPoolExecutor(8) as pool:
        return sorted(pool.map(put, range(count)), key=lambda e: e.task)


def test_aggregate_never_lists(role, batch, tmp_path):
    entries = put_results(role, batch, 1000)
    aggregate = role("aggregate")
    counter = ListCounter(aggregate)
    reference = put_index(role("indexer"), entries, HEADER)

    opened = 0
    entries = read_index(aggregate, reference, TENANT)
    for entry, path in open_results(aggregate, entries, tmp_path):
        assert path.read_bytes() == f"result {int(entry.task[-4:])}".encode()
        opened += 1
    assert opened == 1000
    assert counter.calls == 0


@pytest.mark.parametrize("damage", ["delete", "flip-byte"])
def test_missing_or_corrupt_result_names_its_entry(store, role, batch, tmp_path, damage):
    entries = put_results(role, batch, 3)
    victim = entries[1]
    bucket, _, key = victim.reference.uri.removeprefix("s3://").partition("/")
    admin = s3_client(store.endpoint, store.credentials["admin"])
    if damage == "delete":
        admin.delete_object(Bucket=bucket, Key=key)
    else:
        size = victim.reference.size
        admin.put_object(Bucket=bucket, Key=key, Body=b"x" * size)
    aggregate = role("aggregate")
    reference = put_index(role("indexer"), entries, HEADER)

    seen = []
    with pytest.raises(ArtifactLost) as lost:
        for entry, _ in open_results(aggregate, read_index(aggregate, reference, TENANT), tmp_path):
            seen.append(entry.task)
    assert seen == [entries[0].task]
    assert f"result of task {victim.task}" in str(lost.value)
    assert lost.value.cause.code == (
        "ARTIFACT_NOT_FOUND" if damage == "delete" else "ARTIFACT_CORRUPT"
    )
    assert lost.value.reason.startswith("ARTIFACT_LOST: result of task ")
    assert list(tmp_path.iterdir()) == []
