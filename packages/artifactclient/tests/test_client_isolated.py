"""A store on a size capped volume of its own, which only runs under `-m isolated`."""

import time

import pytest
from wdf_artifactclient.client import PART_BYTES, ArtifactClient, RetryPolicy
from wdf_artifactclient.errors import OutputUploadFailed
from wdf_events.models import ProducerRef
from wdf_storecheck.fullness import HEARTBEAT_SECONDS, OBJECT, FullDisk, fill

pytestmark = [pytest.mark.integration, pytest.mark.isolated]

CAP_MIB = 128
JOB = "123e4567-e89b-42d3-a456-426614174000"
PRODUCER = ProducerRef(job=JOB, task="t0", attempt="r0a1")


@pytest.mark.parametrize("product", ["seaweedfs", "rustfs"])
def test_full_store_maps_to_output_upload_failed(start_capped, product):
    with start_capped(product, CAP_MIB) as started:
        sleeps: list[float] = []
        policy = RetryPolicy(sleep=sleeps.append)
        client = ArtifactClient(started.endpoint, started.credentials["aggregate"], policy)
        target = started.store()
        target.create_buckets()
        kept = {
            name: client.put_attempt(
                name.encode() * 1000, "result", name, PRODUCER, "acme", "b1", 1
            )
            for name in ("first.bin", "second.bin")
        }
        try:
            time.sleep(HEARTBEAT_SECONDS)
            run = FullDisk()
            fill(target, "wdf-results", run)
            assert run.refusal[1] == 500

            # An object of the size that filled the volume, since a few bytes can still fit.
            for size in (OBJECT, 2 * PART_BYTES + 1):
                sleeps.clear()
                with pytest.raises(OutputUploadFailed) as failed:
                    client.put_attempt(bytes(size), "result", "late.bin", PRODUCER, "acme", "b1", 1)
                assert failed.value.reason.startswith("OUTPUT_UPLOAD_FAILED: ")
                assert len(sleeps) == policy.tries - 1

            for name, reference in kept.items():
                assert client.get_bytes(reference, 1_000_000) == name.encode() * 1000
        finally:
            target.sweep()
