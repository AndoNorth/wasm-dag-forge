"""The base and access modes against both products, one container at a time."""

import pytest
from botocore.exceptions import ClientError
from wdf_storecheck.checks import Report, matrix_differences, observe_matrix, run_credentials
from wdf_storecheck.cli import run
from wdf_storecheck.s3 import error_code, status

pytestmark = pytest.mark.integration


def test_base_checks_14_of_14(store):
    lines: list[str] = []
    assert run(store.endpoint, store.credentials, "base", say=lines.append) == 0, lines
    assert "14 of 14 base checks passed" in lines
    assert not [line for line in lines if line.startswith("FAIL")]


def test_role_matrix_equals_the_contract(store):
    target = store.store()
    try:
        seen, leftovers = observe_matrix(target)
    finally:
        target.sweep()
    assert len(seen) == 8 * 4 * 5
    assert matrix_differences(seen) == []
    assert leftovers == []


def test_wrong_role_write_is_refused_and_leaves_nothing(store):
    target = store.store()
    target.create_buckets()
    key = f"{target.prefix}wrong-role"
    try:
        with pytest.raises(ClientError) as refused:
            target.as_role("submit").put_object(Bucket="wdf-results", Key=key, Body=b"1")
        assert error_code(refused.value) == "AccessDenied"
        assert status(refused.value) == 403
        assert not target.exists("wdf-results", key)
        assert target.admin.list_objects_v2(Bucket="wdf-results", Prefix=key)["KeyCount"] == 0
    finally:
        target.sweep()


def test_a_wrong_secret_and_an_anonymous_client_are_refused(store):
    target = store.store()
    target.create_buckets()
    lines: list[str] = []
    run_credentials(target, Report(lines.append))
    assert lines == ["ok access wrong-secret", "ok access anonymous"]


def test_access_mode_has_no_failure_and_no_known_gap(store):
    lines: list[str] = []
    assert run(store.endpoint, store.credentials, "access", store.product, lines.append) == 0
    assert not [line for line in lines if line.startswith(("FAIL", "known-gap"))], lines
