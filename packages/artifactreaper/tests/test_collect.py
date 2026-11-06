"""The collection of content-addressed blobs against the real callers of the index.

Every row is written by the caller that the epic names for it, with a clock that the test moves, so
the ages that the collector judges are the ones those callers stamped. Each collector step is one
call of `collect_content`, and the tombstone interval is crossed by moving the clock.
"""

import hashlib
import subprocess
import sys
import threading
import uuid
from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb
from wdf_artifactclient import cache
from wdf_artifactclient.aggregate import put_index
from wdf_artifactclient.commit import commit
from wdf_artifactclient.errors import ArtifactNotFound
from wdf_artifactclient.models import IndexEntry
from wdf_artifactindex import RowPurged, index_reference, link_use, uses_of
from wdf_artifactreaper.content import PURGE_LOCKED, collect_content
from wdf_artifactreaper.reap import load_windows, reap_once
from wdf_stateops.invariants import check_invariants

pytestmark = [pytest.mark.integration, pytest.mark.pair]

WINDOWS = load_windows()
GRACE = timedelta(days=WINDOWS.content.unreferenced_grace_days)
INTERVAL = timedelta(hours=WINDOWS.content.tombstone_interval_hours)
VERSIONS = {"componentize_py_version": "1", "wit_version": "1"}


@pytest.fixture
def collect(reaper, store):
    """One collector call at a time, returning (tombstoned, purged)."""

    def run(at, hooks=None, conn=None):
        return collect_content(conn or reaper, store, at, WINDOWS, lambda: None, False, hooks)

    return run


@pytest.fixture
def run(reaper, store):
    """A whole reaper run at a time."""
    return lambda at, **kwargs: reap_once(reaper, store, at, WINDOWS, **kwargs)


def state(world, ref) -> tuple[bool, bool] | None:
    """Whether the row of the reference is tombstoned and whether it is purged."""
    return world.db.execute(
        "SELECT deleted_at IS NOT NULL, purged_at IS NOT NULL FROM artifact WHERE uri = %s",
        (ref.uri,),
    ).fetchone()


def present(world, ref) -> bool:
    try:
        world.workflow.head(ref)
    except ArtifactNotFound:
        return False
    return True


def compiled(world, artifacts, job, label):
    """A component and its build-info, committed as the record activity does for an attempt."""
    wasm = world.compile.put_content(world.payload(label), "component", "component.wasm")
    info = world.compile.put_content(world.payload(f"{label} info"), "build-info", "build.json")
    commit(artifacts, world.workflow, [wasm, info], job, "compile-0")
    return wasm, info


def key_of(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def test_a_wasm_named_by_a_live_cache_entry_is_never_collected(world, artifacts, clock, collect):
    wasm, info = compiled(world, artifacts, str(uuid.uuid4()), "cached")
    assert isinstance(cache.record(artifacts, wasm, info, key_of("cached"), VERSIONS), cache.Won)

    clock.advance(days=60)
    assert collect(clock.now) == (0, 0)
    clock.advance(days=60)
    assert collect(clock.now) == (0, 0)
    assert [state(world, r) for r in (wasm, info)] == [(False, False)] * 2
    assert present(world, wasm) and present(world, info)

    cache.invalidate(artifacts, key_of("cached"), clock.now)
    assert collect(clock.now) == (2, 0)
    clock.advance(hours=2)
    assert collect(clock.now) == (0, 2)
    assert not present(world, wasm) and not present(world, info)


def test_the_loser_of_a_cache_race_is_collected_after_the_grace(world, artifacts, clock, collect):
    key = key_of("race")
    first, first_info = compiled(world, artifacts, str(uuid.uuid4()), "winner")
    second, second_info = compiled(world, artifacts, str(uuid.uuid4()), "loser")
    assert isinstance(cache.record(artifacts, first, first_info, key, VERSIONS), cache.Won)
    assert isinstance(cache.record(artifacts, second, second_info, key, VERSIONS), cache.Lost)

    clock.advance(hours=47)
    assert collect(clock.now) == (0, 0)

    clock.advance(hours=2)
    assert collect(clock.now) == (2, 0)
    assert state(world, second) == (True, False)
    assert state(world, first) == (False, False)

    clock.advance(hours=2)
    assert collect(clock.now) == (0, 2)
    assert not present(world, second) and not present(world, second_info)
    assert present(world, first) and present(world, first_info)
    hit = cache.lookup(artifacts, world.workflow, key)
    assert isinstance(hit, cache.Hit)
    assert hit.wasm.uri == first.uri


def test_nothing_younger_than_the_grace_is_tombstoned(world, artifacts, clock, collect):
    ref = world.submit.put_content(
        world.payload("young"), "manifest", "m.json", conn=artifacts, job_id=str(uuid.uuid4())
    )

    clock.advance(days=2, minutes=-5)
    assert collect(clock.now) == (0, 0)
    assert state(world, ref) == (False, False)

    clock.advance(minutes=10)
    assert collect(clock.now) == (1, 0)
    assert state(world, ref) == (True, False)


def test_a_paused_jobs_inputs_are_kept(world, artifacts, clock, run, collect):
    """One blob of every input class of the table, each written by its named caller."""
    job, other = str(uuid.uuid4()), str(uuid.uuid4())
    world.job("succeeded", clock.now, job_id=other)
    submit = world.submit

    def dispatched(label, kind):
        return submit.put_content(
            world.payload(label), kind, f"{label}.bin", conn=artifacts, job_id=job
        )

    manifest, bundle = dispatched("manifest", "manifest"), dispatched("source", "source")
    supplied = dispatched("supplied index", "index")
    world.job("paused", job_id=job, manifest=manifest.model_dump())
    patched = dispatched("patched manifest", "manifest")
    world.db.execute(
        "UPDATE accepted_job SET pending_patch_ref = %s WHERE job_id = %s",
        (Jsonb(patched.model_dump()), job),
    )
    result = world.result(job)
    entry = IndexEntry(task="sim", status="succeeded", reference=result)
    header = {"job": job, "tenant": world.tenant, "task": "aggregate-0", "partial": "refuse"}
    built = put_index(world.indexer, [entry], header)
    commit(artifacts, world.workflow, [built], job, "aggregate-0")
    wasm, info = compiled(world, artifacts, job, "attempt")

    cached, cached_info = compiled(world, artifacts, other, "cached")
    key = key_of("paused")
    assert isinstance(cache.record(artifacts, cached, cached_info, key, VERSIONS), cache.Won)
    hit = cache.lookup(artifacts, world.workflow, key)
    assert isinstance(hit, cache.Hit)
    cache.use(artifacts, hit, job)
    cache.invalidate(artifacts, key, clock.now)

    held = [manifest, bundle, supplied, patched, built, wasm, info, cached, cached_info]
    clock.advance(days=100)
    report = run(clock.now)
    assert (report.tombstoned, report.purged, report.orphans) == (0, 0, 0)
    assert [state(world, r) for r in held] == [(False, False)] * len(held)
    assert all(present(world, r) for r in held)

    world.move(job, "succeeded", clock.now)
    clock.advance(days=3)
    assert collect(clock.now) == (len(held), 0)
    clock.advance(hours=2)
    assert collect(clock.now) == (0, len(held))
    assert not any(present(world, r) for r in held)


def abandoned(world, artifacts, clock, label):
    """A blob that an earlier submission linked and nothing holds any longer, in a start state."""
    data = world.payload(label)
    submit = world.submit
    ref = submit.put_content(data, "manifest", "m.json", conn=artifacts, job_id=str(uuid.uuid4()))
    clock.advance(days=10)
    return data, ref


def interleavings(a: list[str], b: list[str]):
    """Every merge of the two sequences that keeps the order of each."""
    if not a or not b:
        yield a + b
        return
    for rest in interleavings(a[1:], b):
        yield [a[0], *rest]
    for rest in interleavings(a, b[1:]):
        yield [b[0], *rest]


ORDERS = list(interleavings(["index", "upload", "link", "job"], ["tombstone", "purge"]))


@pytest.mark.parametrize("start", ["unreferenced", "tombstoned", "purged"])
@pytest.mark.parametrize("order", ORDERS, ids="-".join)
def test_reap_races_resubmit(order, start, world, artifacts, clock, collect):
    """A resubmit of identical bytes against two collector passes, in every ordering."""
    data, ref = abandoned(world, artifacts, clock, "resubmit")
    if start != "unreferenced":
        collect(clock.now)
    if start == "purged":
        clock.advance(hours=2)
        assert collect(clock.now) == (0, 1)
        assert not present(world, ref)
    job = str(uuid.uuid4())

    def index():
        index_reference(artifacts, ref)

    def upload():
        world.submit.put_content(data, "manifest", "m.json")

    def link():
        while isinstance(link_use(artifacts, job, ref.uri, clock.now), RowPurged):
            index()
            upload()

    steps = {
        "index": index,
        "upload": upload,
        "link": link,
        "job": lambda: world.job("accepted", job_id=job, manifest=ref.model_dump()),
        "tombstone": lambda: collect(clock.now),
        "purge": lambda: collect(clock.now),
    }
    for name in order:
        if name in ("tombstone", "purge"):
            clock.advance(hours=2)
        else:
            clock.advance(minutes=1)
        steps[name]()

    assert state(world, ref) == (False, False)
    assert present(world, ref)
    assert job in [use.job_id for use in uses_of(artifacts, ref.uri)]
    clock.advance(days=30)
    assert collect(clock.now) == (0, 0)
    assert present(world, ref)
    assert check_invariants(world.db).violations == []


def test_purge_is_skipped_after_a_revive(world, artifacts, clock, collect):
    _, ref = abandoned(world, artifacts, clock, "revived")
    assert collect(clock.now) == (1, 0)
    assert state(world, ref) == (True, False)

    clock.advance(minutes=30)
    job = str(uuid.uuid4())
    committed = commit(artifacts, world.workflow, [ref], job, "t0")
    assert committed.revived == (ref.uri,)

    clock.advance(hours=2)
    assert collect(clock.now) == (0, 0)
    assert state(world, ref) == (False, False)
    assert present(world, ref)


def test_link_then_tombstone_then_job_insert_leaves_the_row_live(world, artifacts, clock, collect):
    data, ref = abandoned(world, artifacts, clock, "linked")
    job = str(uuid.uuid4())

    world.submit.put_content(data, "manifest", "m.json", conn=artifacts, job_id=job)
    clock.advance(minutes=10)
    assert collect(clock.now) == (0, 0)
    world.job("accepted", job_id=job, manifest=ref.model_dump())

    clock.advance(days=30)
    assert collect(clock.now) == (0, 0)
    assert state(world, ref) == (False, False)
    assert present(world, ref)


def test_stale_use_row_then_retry_then_tombstone_then_job_insert_leaves_the_row_live(
    world, artifacts, clock, collect
):
    job = str(uuid.uuid4())
    data = world.payload("retried")
    ref = world.submit.put_content(data, "manifest", "m.json", conn=artifacts, job_id=job)
    clock.advance(days=10)

    world.submit.put_content(data, "manifest", "m.json", conn=artifacts, job_id=job)
    (use,) = uses_of(artifacts, ref.uri)
    assert use.last_linked_at == clock.now
    clock.advance(minutes=10)
    assert collect(clock.now) == (0, 0)
    world.job("accepted", job_id=job, manifest=ref.model_dump())

    clock.advance(days=30)
    assert collect(clock.now) == (0, 0)
    assert state(world, ref) == (False, False)
    assert present(world, ref)


def test_a_link_waiting_on_the_purge_lock_is_refused_and_the_bytes_put_again(
    world, artifacts, clock, collect, login
):
    data, ref = abandoned(world, artifacts, clock, "locked")
    collect(clock.now)
    clock.advance(hours=2)
    job = str(uuid.uuid4())
    other = login("wdf_artifacts")
    outcome: list[object] = []

    def link_when_locked(uri: str) -> None:
        waiting = threading.Thread(
            target=lambda: outcome.append(link_use(other, job, uri, clock.now))
        )
        waiting.start()
        waiting.join(2)
        assert waiting.is_alive(), "the link did not wait for the row lock"
        hold.append(waiting)

    hold: list[threading.Thread] = []
    assert collect(clock.now, hooks={PURGE_LOCKED: link_when_locked}) == (0, 1)
    hold[0].join(30)

    assert outcome == [RowPurged()]
    assert not present(world, ref)
    assert job not in [use.job_id for use in uses_of(artifacts, ref.uri)]
    world.submit.put_content(data, "manifest", "m.json", conn=artifacts, job_id=job)
    assert state(world, ref) == (False, False)
    assert present(world, ref)


def test_a_full_run_on_a_store_holding_every_kind_leaves_the_invariants_holding(
    world, artifacts, clock, run, appdb
):
    done = world.job("succeeded", clock.now - timedelta(days=9))
    live = world.job("running")
    world.result(done)
    world.summary(done)
    for segment in range(5):
        world.checkpoint(live, segment)
    for job in (done, live):
        world.submit.put_content(
            world.payload(f"manifest {job}"), "manifest", "m.json", conn=artifacts, job_id=job
        )
        compiled(world, artifacts, job, f"component {job}")
    clock.advance(days=3)

    first = run(clock.now)
    clock.advance(hours=2)
    second = run(clock.now)

    assert first.results_jobs == 1
    assert second.purged >= 1
    checked = subprocess.run(
        [sys.executable, "-m", "wdf_stateops.invariants", "--dsn", appdb],
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stdout
    assert "invariant:" not in checked.stdout
