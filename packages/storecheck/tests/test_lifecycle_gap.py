"""A credential that can write a bucket cannot replace the lifecycle rules of that bucket."""

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


def test_writers_cannot_edit_lifecycle_on_either_product(store):
    target = store.store()
    target.create_buckets()
    refused = lifecycle_lines(target, store.product)
    assert [line.split(":")[0] for line in refused] == [
        f"ok access lifecycle-edit {role}" for role in WRITERS
    ]
    with pytest.raises(ClientError) as untouched:
        target.admin.get_bucket_lifecycle_configuration(Bucket="wdf-sources")
    assert error_code(untouched.value) == "NoSuchLifecycleConfiguration"
    target.sweep()
