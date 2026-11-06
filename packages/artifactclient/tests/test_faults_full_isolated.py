"""The scenario of a full store, on a size capped volume of its own."""

import time

import pytest
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.errors import OutputUploadFailed
from wdf_events.models import ProducerRef
from wdf_storecheck.fullness import HEARTBEAT_SECONDS, OBJECT, FullDisk, fill

pytestmark = [pytest.mark.integration, pytest.mark.isolated]

CAP_MIB = 128
JOB = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture(scope="module")
def seaweed(start_capped):
    with start_capped("seaweedfs", CAP_MIB) as started:
        started.store().create_buckets()
        yield started


def test_a_full_store_gives_output_upload_failed(seaweed):
    client = ArtifactClient(
        seaweed.endpoint, seaweed.credentials["simulate"], RetryPolicy(sleep=lambda _: None)
    )
    target = seaweed.store()
    try:
        time.sleep(HEARTBEAT_SECONDS)
        run = FullDisk()
        fill(target, "wdf-results", run)
        with pytest.raises(OutputUploadFailed) as failed:
            client.put_attempt(
                bytes(OBJECT),
                "result",
                "late.bin",
                ProducerRef(job=JOB, task="t0", attempt="r0a1"),
                "acme",
                "b1",
                1,
            )
        assert failed.value.reason.startswith("OUTPUT_UPLOAD_FAILED: ")
    finally:
        target.sweep()
