"""Falling back to the previous checkpoint: the index names it and the store holds its bytes."""

import hashlib

import psycopg
import pytest
from wdf_artifactclient.checkpoint import verify_checkpoint, write_checkpoint
from wdf_artifactclient.client import ArtifactClient
from wdf_artifactclient.errors import CheckpointCorrupt
from wdf_artifactclient.keys import split_uri
from wdf_artifactindex import checkpoints_of, index_reference, previous_checkpoint
from wdf_events.models import CheckpointReference, ProducerRef
from wdf_storecheck.fixtures import running
from wdf_storecheck.s3 import client as s3_client

pytestmark = [pytest.mark.integration, pytest.mark.pair]

JOB = "223e4567-e89b-42d3-a456-426614174000"
META = {"format_version": 1, "progress": 0, "wasm_sha256": hashlib.sha256(b"c").hexdigest()}


@pytest.fixture(scope="module")
def seaweed(t1, tmp_path_factory):
    """One SeaweedFS container for this module, which is gone before the next module runs."""
    with running(t1, "seaweedfs", tmp_path_factory.mktemp("store")) as started:
        started.store().create_buckets()
        yield started


def test_previous_checkpoint_after_corruption(seaweed, appdb):
    worker = ArtifactClient(seaweed.endpoint, seaweed.credentials["simulate"])
    admin = s3_client(seaweed.endpoint, seaweed.credentials["admin"])
    references = [
        write_checkpoint(
            worker,
            hashlib.shake_256(f"state {n}".encode()).digest(2048),
            ProducerRef(job=JOB, task="sim", attempt="r0a1"),
            "acme",
            "b1",
            0,
            str(n),
            {**META, "progress": n},
        )
        for n in range(3)
    ]
    with psycopg.connect(appdb) as conn:
        for reference in references:
            index_reference(conn, reference)
        assert [row.uri for row in checkpoints_of(conn, JOB, "sim")] == [
            r.uri for r in reversed(references)
        ]

        newest = references[-1]
        data = verify_checkpoint(worker, newest).objects[0]
        bucket, key = split_uri(data.uri)
        body = admin.get_object(Bucket=bucket, Key=key)["Body"].read()
        admin.put_object(Bucket=bucket, Key=key, Body=body[:-1] + bytes([body[-1] ^ 1]))
        with pytest.raises(CheckpointCorrupt):
            verify_checkpoint(worker, newest)

        fallback = previous_checkpoint(conn, newest.uri)
    assert fallback is not None and fallback.uri == references[1].uri
    reference = CheckpointReference.model_validate(
        {
            "uri": fallback.uri,
            "sha256": fallback.sha256,
            "size": fallback.size,
            "name": fallback.name,
            "kind": "checkpoint",
            "producer": {"job": JOB, "task": "sim", "attempt": fallback.producer_attempt},
        }
    )
    assert verify_checkpoint(worker, reference).progress == 1
