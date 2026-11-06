"""The orphan sweep, which lists a whole bucket and so runs against a store of its own.

The objects are written by the real callers and the clock is the test's, so ages are judged the way
the collector judges them. The last-modified time of an object is the store's own, which is the
moment of the test, so a sweep run before the orphan grace is one at the start time plus less.
"""

import uuid
from datetime import timedelta

import pytest
from wdf_artifactclient.commit import commit
from wdf_artifactclient.errors import ArtifactNotFound
from wdf_artifactindex import Deleted, Purged, purge, tombstone
from wdf_artifactreaper import content
from wdf_artifactreaper.content import (
    CONTENT_BUCKETS,
    SWEEP_BEFORE_LOCK,
    collect_content,
    sweep_orphans,
)
from wdf_artifactreaper.reap import load_windows
from wdf_storecheck.fixtures import running

pytestmark = [pytest.mark.integration, pytest.mark.pair, pytest.mark.isolated]

WINDOWS = load_windows()
ORPHAN_GRACE = timedelta(days=WINDOWS.content.orphan_grace_days)


@pytest.fixture(scope="module")
def shelf(t1, tmp_path_factory):
    """A SeaweedFS container for this module alone, so that a bucket listing is only its objects."""
    with running(t1, "seaweedfs", tmp_path_factory.mktemp("orphans")) as started:
        started.store().create_buckets()
        yield started


@pytest.fixture
def collect(reaper, store):
    return lambda at: collect_content(reaper, store, at, WINDOWS, lambda: None, False)


@pytest.fixture
def sweep(reaper, store):
    def run(at, hooks=None, dry_run=False):
        return sweep_orphans(reaper, store, at, WINDOWS, lambda: None, dry_run, hooks)

    return run


def present(world, ref) -> bool:
    try:
        world.workflow.head(ref)
    except ArtifactNotFound:
        return False
    return True


def row(world, ref):
    return world.db.execute(
        "SELECT deleted_at IS NOT NULL, purged_at IS NOT NULL FROM artifact WHERE uri = %s",
        (ref.uri,),
    ).fetchone()


def dispatched(world, artifacts, label, kind="manifest"):
    """Bytes put by the dispatch API for a job that never gets a row, which nothing holds later."""
    return world.submit.put_content(
        world.payload(label), kind, f"{label}.bin", conn=artifacts, job_id=str(uuid.uuid4())
    )


def test_orphan_object_is_deleted_after_the_grace(world, artifacts, clock, reaper, store, sweep):
    grace = timedelta(days=WINDOWS.content.unreferenced_grace_days)
    interval = timedelta(hours=WINDOWS.content.tombstone_interval_hours)
    bare = world.submit.put_content(world.payload("no row"), "manifest", "m.json")
    wasm = world.compile.put_content(world.payload("component"), "component", "c.wasm")
    tombstoned = dispatched(world, artifacts, "tombstoned")
    purged = dispatched(world, artifacts, "purged")
    later = clock.advance(days=3)
    for ref in (tombstoned, purged):
        assert isinstance(tombstone(reaper, ref.uri, later, grace), Deleted)
    clock.advance(hours=2)
    with reaper.transaction():
        assert isinstance(purge(reaper, purged.uri, clock.now, interval, grace), Purged)
        store.delete_object(Bucket="wdf-sources", Key=purged.uri.removeprefix("s3://wdf-sources/"))
    world.submit.put_content(world.payload("purged"), "manifest", "purged.bin")
    assert row(world, purged) == (True, True) and present(world, purged)

    start = clock.now - timedelta(days=3, hours=2)
    assert sweep(start + ORPHAN_GRACE - timedelta(hours=1)) == 0
    assert all(present(world, r) for r in (bare, wasm, tombstoned, purged))

    assert sweep(clock.now) == 4
    assert not any(present(world, r) for r in (bare, wasm, tombstoned, purged))
    assert row(world, tombstoned) == (True, True)
    assert sweep(clock.now) == 0


def test_a_committed_object_and_a_young_object_are_kept(world, artifacts, clock, sweep):
    job = str(uuid.uuid4())
    wasm = world.compile.put_content(world.payload("committed"), "component", "c.wasm")
    commit(artifacts, world.workflow, [wasm], job, "compile-0")
    young = world.submit.put_content(world.payload("young"), "manifest", "m.json")

    assert sweep(clock.now + timedelta(days=1)) == 0
    assert present(world, wasm) and present(world, young)

    assert sweep(clock.now + timedelta(days=30)) == 1
    assert present(world, wasm) and row(world, wasm) == (False, False)
    assert not present(world, young)


def test_sweep_skips_an_object_whose_row_went_live(world, artifacts, clock, collect, sweep):
    tombstoned = dispatched(world, artifacts, "revived while listed")
    clock.advance(days=3)
    assert collect(clock.now) == (1, 0)
    clock.advance(hours=2)
    job = str(uuid.uuid4())

    def revive(uri: str) -> None:
        commit(artifacts, world.workflow, [tombstoned], job, "t0")

    assert sweep(clock.now, hooks={SWEEP_BEFORE_LOCK: revive}) == 0
    assert row(world, tombstoned) == (False, False)
    assert present(world, tombstoned)


def test_sweep_does_not_delete_a_tombstoned_object_inside_the_tombstone_interval(
    world, artifacts, clock, collect, sweep
):
    tombstoned = dispatched(world, artifacts, "inside the interval")
    clock.advance(days=3)
    assert collect(clock.now) == (1, 0)

    clock.advance(minutes=30)
    assert sweep(clock.now) == 0
    assert row(world, tombstoned) == (True, False) and present(world, tombstoned)

    clock.advance(hours=1)
    assert sweep(clock.now) == 1
    assert row(world, tombstoned) == (True, True) and not present(world, tombstoned)


def test_orphan_sweep_continues_from_its_cursor(world, clock, sweep, monkeypatch):
    monkeypatch.setattr(content, "OBJECT_BATCH", 3)
    monkeypatch.setattr(content, "SWEEP_PAGES", 1)
    for n in range(8):
        world.submit.put_content(world.payload(f"orphan {n}"), "manifest", "m.json")
    later = clock.now + timedelta(days=3)

    deleted = [sweep(later) for _ in range(3)]

    assert deleted == [3, 3, 2]
    assert world.keys("wdf-sources", "") == []
    position = world.db.execute(
        "SELECT position FROM reaper_cursor WHERE name = 'orphan-sweep:wdf-sources'"
    ).fetchone()
    assert position == ("",)


def test_orphan_sweep_dry_run_counts_and_leaves_objects_and_cursor(world, clock, sweep):
    world.submit.put_content(world.payload("counted"), "manifest", "m.json")
    world.compile.put_content(world.payload("counted component"), "component", "c.wasm")
    later = clock.now + timedelta(days=3)

    assert sweep(later, dry_run=True) == 2
    assert [len(world.keys(bucket, "")) for bucket in CONTENT_BUCKETS] == [1, 1]
    assert world.db.execute("SELECT count(*) FROM reaper_cursor").fetchone() == (0,)
    assert sweep(later) == 2
