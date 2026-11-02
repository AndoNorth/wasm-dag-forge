"""Rules that expire objects, the rule of days that cannot be waited for, and the other modes."""

import json
import re

import pytest
from botocore.exceptions import ClientError
from wdf_storecheck.cli import Options, run
from wdf_storecheck.s3 import error_code

pytestmark = pytest.mark.integration


def lines_of(store, mode, **options):
    lines: list[str] = []
    code = run(
        store.endpoint, store.credentials, mode, store.product, lines.append, Options(**options)
    )
    return code, lines


@pytest.mark.parametrize("product", ["rustfs"])
def test_lifecycle_date_rule_expires_only_the_armed_prefix(start, product):
    with start(product) as store:
        code, lines = lines_of(store, "lifecycle", wait=120)
        assert code == 0, lines
        assert "ok lifecycle: expired: 1 of 1, controls kept: 3 of 3" in lines
        with pytest.raises(ClientError) as untouched:
            store.store().admin.get_bucket_lifecycle_configuration(Bucket="wdf-sources")
        assert error_code(untouched.value) in (
            "NoSuchLifecycleConfiguration",
            "NoSuchBucketLifecycleConfiguration",
        )


def test_days_check_at_once_refuses_and_a_due_check_finds_the_object_not_expired(store, tmp_path):
    state = tmp_path / "days.json"
    code, lines = lines_of(store, "days-arm", state=state)
    assert code == 0, lines
    document = json.loads(state.read_text())
    assert document["due"] > 0
    code, lines = lines_of(store, "days-check", state=state)
    assert code == 1
    assert re.match(r"FAIL days-check: not yet due", lines[0])
    state.write_text(json.dumps({**document, "due": 1.0}))
    code, lines = lines_of(store, "days-check", state=state)
    assert code == 1
    assert "FAIL days-check expired: still there" in lines
    store.store().sweep()


def test_marker_put_then_get_and_a_missing_marker_fails(store):
    assert lines_of(store, "marker-get")[0] == 1
    assert lines_of(store, "marker-put")[0] == 0
    assert lines_of(store, "marker-get")[0] == 0
    store.store().admin.put_object(Bucket="wdf-sources", Key="storecheck-marker/object", Body=b"x")
    assert lines_of(store, "marker-get")[0] == 1
    store.store().admin.delete_object(Bucket="wdf-sources", Key="storecheck-marker/object")


def test_capacity_prints_a_line_per_bucket_and_needs_the_reaper(store):
    target = store.store()
    target.create_buckets()
    code, lines = lines_of(store, "capacity")
    assert code == 0
    assert [line.split()[:2] for line in lines] == [["bytes", b] for b in target.buckets]
    without = {role: c for role, c in store.credentials.items() if role != "reaper"}
    lines = []
    assert run(store.endpoint, without, "capacity", say=lines.append) == 1
    assert lines == ["FAIL capacity: the credentials file has no reaper identity"]
