"""A size-capped volume filled to the first refused put, on both products."""

import pytest
from wdf_storecheck.checks import contract_gaps
from wdf_storecheck.fullness import (
    GAP,
    free_outside_s3,
    observe_full_disk,
    vacuum_through,
)
from wdf_storecheck.s3 import ALLOWED

pytestmark = pytest.mark.integration

CAP_MIB = 128


ATTEMPTS = 3


@pytest.fixture(scope="module")
def runs(start_capped):
    """The whole full-disk sequence of each product, so the cases below read one run each.

    SeaweedFS learns of its free space from a heartbeat, so a fast fill can overshoot into the
    room that its metadata needs, and then even a delete is refused.
    The vacuum that follows is also reported through that heartbeat, so a slow host can leave the
    store refusing after the settle window.
    Neither run says anything about the product, so the sequence starts again on a fresh volume.
    """
    seen = {}
    for product in ("seaweedfs", "rustfs"):
        for _ in range(ATTEMPTS):
            with start_capped(product, CAP_MIB) as started:
                prefix = started.exec_prefix()
                recover = (
                    vacuum_through(prefix) if product == "seaweedfs" else free_outside_s3(prefix)
                )
                seen[product] = observe_full_disk(started.store(), product, recover)
            if product == "rustfs" or (
                seen[product].delete == ALLOWED and seen[product].put_after_recovery == ALLOWED
            ):
                break
    return seen


@pytest.mark.parametrize("product", ["seaweedfs", "rustfs"])
def test_disk_full_refuses_with_500_and_keeps_survivors(runs, product):
    run = runs[product]
    assert run.refusal == ("InternalError", 500)
    assert run.second_refusal == ("InternalError", 500)
    assert 20 < len(run.written) <= CAP_MIB // 4
    assert run.survivors_exact == len(run.written)


def test_batch_delete_works_while_full_on_seaweedfs(runs):
    assert runs["seaweedfs"].delete == ALLOWED


def test_recover_needs_vacuum_on_seaweedfs(runs):
    run = runs["seaweedfs"]
    assert run.put_after_delete == "refused"
    assert run.put_after_recovery == ALLOWED


def test_delete_fails_while_full_on_rustfs(runs):
    run = runs["rustfs"]
    assert run.delete != ALLOWED
    assert "reaper" in contract_gaps("rustfs")[GAP]
    assert run.put_after_delete == "refused"
    assert run.put_after_recovery == ALLOWED
