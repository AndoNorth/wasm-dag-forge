"""A credential that can write a bucket and what it may do to the lifecycle rules of that bucket."""

import pytest
from botocore.exceptions import ClientError
from wdf_storecheck.checks import Report, run_lifecycle_edit
from wdf_storecheck.s3 import error_code

pytestmark = pytest.mark.integration

WRITERS = ["submit", "indexer", "compile", "simulate", "aggregate"]


def lifecycle_lines(target, product) -> list[str]:
    lines: list[str] = []
    run_lifecycle_edit(target, Report(lines.append), product)
    return lines


def test_writer_can_edit_lifecycle_is_a_known_gap_on_seaweedfs(start):
    """Only the coarse Write action of SeaweedFS gives the gap, so the identities use it."""
    with start("seaweedfs", legacy=True) as running:
        target = running.store()
        target.create_buckets()
        recorded = lifecycle_lines(target, "seaweedfs")
        assert [line.split(":")[0] for line in recorded] == [
            f"known-gap lifecycle-edit-by-writer {role}" for role in WRITERS
        ]
        unrecorded = lifecycle_lines(target, "rustfs")
        assert all(line.startswith("FAIL access lifecycle-edit") for line in unrecorded)
        assert len(unrecorded) == len(WRITERS)
        with pytest.raises(ClientError) as untouched:
            target.admin.get_bucket_lifecycle_configuration(Bucket="wdf-sources")
        assert error_code(untouched.value) in ("NoSuchLifecycleConfiguration",)


def test_writer_cannot_edit_lifecycle_on_rustfs(start):
    with start("rustfs") as running:
        target = running.store()
        target.create_buckets()
        refused = lifecycle_lines(target, "rustfs")
        assert [line.split(":")[0] for line in refused] == [
            f"ok access lifecycle-edit {role}" for role in WRITERS
        ]
        flipped = lifecycle_lines(target, "seaweedfs")
        assert all(line.startswith("FAIL access lifecycle-edit") for line in flipped)
        assert len(flipped) == len(WRITERS)
