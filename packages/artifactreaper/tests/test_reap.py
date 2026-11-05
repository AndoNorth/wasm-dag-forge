"""The passes of one reaper run against a real store and database."""

import threading
from datetime import timedelta

import pytest
from wdf_artifactclient.checkpoint import verify_checkpoint
from wdf_artifactindex import CacheEntry, index_reference, lookup_cache_entry, record_cache_entry
from wdf_artifactreaper.reap import load_windows
from wdf_events.models import ContentReference

pytestmark = [pytest.mark.integration, pytest.mark.pair]

WINDOWS = load_windows()


def test_paused_and_admitted_jobs_are_never_touched_by_any_pass(world, reap, ago):
    jobs = {status: world.job(status) for status in ("accepted", "admitted", "paused")}
    jobs["resume-pending"] = world.job("resume-pending")
    for job in jobs.values():
        world.result(job)
        world.summary(job)
        for segment in range(WINDOWS.checkpoint_keep + 3):
            world.checkpoint(job, segment)
    results = world.keys("wdf-results")
    checkpoints = world.keys("wdf-checkpoints")

    report = reap(timedelta(days=400))

    assert report.counters() == dict.fromkeys(report.counters(), 0)
    assert world.keys("wdf-results") == results
    assert world.keys("wdf-checkpoints") == checkpoints
    assert all(world.deleted_rows(job) == 0 for job in jobs.values())


def test_results_and_checkpoint_windows_apply_separately(world, reap, ago):
    job = world.job("succeeded", ago(0))
    world.result(job)
    world.checkpoint(job, 0)

    reap(timedelta(days=2))
    assert world.keys("wdf-checkpoints") == []
    assert len(world.keys("wdf-results")) == 1

    reap(timedelta(days=8))
    assert world.keys("wdf-results") == []


def test_failed_job_is_kept_longer_than_a_succeeded_one(world, reap, ago):
    succeeded, failed = world.job("succeeded", ago(0)), world.job("failed", ago(0))
    world.result(succeeded)
    world.result(failed)

    reap(timedelta(days=8))
    assert [k for k in world.keys("wdf-results") if failed in k]
    assert not [k for k in world.keys("wdf-results") if succeeded in k]

    reap(timedelta(days=15))
    assert world.keys("wdf-results") == []


@pytest.mark.parametrize("status", sorted(WINDOWS.statuses))
def test_window_boundaries_by_status(world, reap, ago, status):
    window = WINDOWS.statuses[status]
    for bucket, days in (
        ("results", window.results_days),
        ("checkpoints", window.checkpoints_days),
    ):
        job = world.job(status, ago(days, seconds=-1))
        world.result(job) if bucket == "results" else world.checkpoint(job, 0)
        before = world.keys(f"wdf-{bucket}")
        assert reap().results_jobs + reap().checkpoint_jobs == 0
        assert world.keys(f"wdf-{bucket}") == before

        reap(timedelta(seconds=1))
        assert world.keys(f"wdf-{bucket}") == []


def test_zombie_prefix_of_a_terminal_job_is_removed(world, reap, ago):
    job = world.job("failed", ago(20))
    world.result(job, attempt="r0a2")
    zombie = world.zombie(job, attempt="r0a1")
    assert zombie in world.keys("wdf-results")

    report = reap()

    assert world.keys("wdf-results") == []
    assert report.deleted_objects == 2
    assert world.live_rows(job) == 0


def test_summary_under_the_aggregate_prefix_is_removed(world, reap, ago):
    job = world.job("succeeded", ago(9))
    summary = world.summary(job)
    assert "/aggregate/" in summary.uri

    reap()

    assert world.keys("wdf-results") == []
    assert world.live_rows(job) == 0


def test_running_job_is_pruned_to_the_keep_count_and_keeps_its_newest(world, reap, ago):
    job = world.job("running")
    other = world.job("running")
    references = [world.checkpoint(job, n) for n in range(5)]
    spare = world.checkpoint(other, 0)

    report = reap()

    assert report.pruned_checkpoints == 3
    assert report.kept_live == 2
    live = [
        row[0]
        for row in world.db.execute(
            "SELECT uri FROM artifact WHERE producer_job = %s AND deleted_at IS NULL"
            " ORDER BY row_id",
            (job,),
        )
    ]
    assert live == [r.uri for r in references[-2:]]
    for kept in references[-2:] + [spare]:
        verify_checkpoint(world.worker, kept)
    gone = [key for key in world.keys("wdf-checkpoints") if f"/{job}/" in key]
    assert len(gone) == 4
    assert world.deleted_rows(job) == 3

    assert reap().pruned_checkpoints == 0


def test_pass_after_a_crash_between_mark_and_delete_completes(world, reap, ago, store):
    job = world.job("succeeded", ago(9))
    world.result(job, seed=0)
    world.result(job, seed=1)
    store.fail_next_delete = True

    with pytest.raises(ConnectionError):
        reap()

    assert world.deleted_rows(job) == 2
    assert len(world.keys("wdf-results")) == 2
    assert world.db.execute(
        "SELECT count(*) FROM reaper_job WHERE job_id = %s", (job,)
    ).fetchone() == (0,)

    report = reap()

    assert world.keys("wdf-results") == []
    assert (report.results_jobs, report.deleted_objects) == (1, 2)
    assert world.db.execute(
        "SELECT results_done_at FROM reaper_job WHERE job_id = %s", (job,)
    ).fetchone() == (ago(0),)


def test_done_marker_skips_a_finished_job_without_listing_it(world, reap, ago, store):
    job = world.job("succeeded", ago(9))
    world.result(job)
    reap()
    store.lists = 0

    report = reap()

    assert store.lists == 0
    assert report.counters() == dict.fromkeys(report.counters(), 0)


def test_selection_excludes_jobs_beyond_the_hard_expiry(world, reap, ago, store):
    job = world.job("succeeded", ago(60))
    world.result(job)
    store.lists = 0

    report = reap()

    assert (report.results_jobs, store.lists) == (0, 0)
    assert len(world.keys("wdf-results")) == 1


def test_heartbeat_is_sent_per_batch_of_objects(world, reap, ago):
    job = world.job("succeeded", ago(9))
    world.bulk(job, 5000)
    beats = []

    report = reap(heartbeat=lambda: beats.append(1))

    assert report.deleted_objects == 5000
    assert len(beats) >= 10
    assert world.keys("wdf-results") == []


def test_two_overlapping_runs_do_not_double_work(world, reap, ago, login):
    job = world.job("succeeded", ago(9))
    world.bulk(job, 1200)
    started, release = threading.Event(), threading.Event()
    reports = {}

    def first_run():
        def beat():
            started.set()
            assert release.wait(60)

        reports["first"] = reap(heartbeat=beat)

    runner = threading.Thread(target=first_run)
    runner.start()
    assert started.wait(60)
    try:
        second = reap(conn=login("wdf_reaper"))
    finally:
        release.set()
        runner.join(60)

    assert not second.ran
    assert second.counters() == dict.fromkeys(second.counters(), 0)
    assert reports["first"].deleted_objects == 1200
    assert world.keys("wdf-results") == []
    assert reap().ran


def test_cache_entries_expire_by_last_use(world, reap, ago):
    def entry(label: str, last_used) -> CacheEntry:
        wasm = world.worker  # the wasm and build-info only need live rows
        refs = []
        for kind in ("component", "build-info"):
            sha = (label + kind).encode().hex().ljust(64, "0")[:64]
            ref = ContentReference(
                uri=f"s3://wdf-components/sha256/{sha[:2]}/{sha}",
                sha256=sha,
                size=1,
                name=kind,
                kind=kind,
            )
            index_reference(world.db, ref)
            refs.append(ref.uri)
        del wasm
        key = (label + "key").encode().hex().ljust(64, "0")[:64]
        entry = CacheEntry(
            cache_key=key,
            wasm_uri=refs[0],
            build_info_uri=refs[1],
            componentize_py_version="1",
            wit_version="1",
            last_used_at=last_used,
        )
        record_cache_entry(world.db, entry)
        return entry

    stale = entry("stale", ago(WINDOWS.cache_unused_days, 1))
    edge = entry("edge", ago(WINDOWS.cache_unused_days))
    fresh = entry("fresh", ago(1))

    report = reap()

    assert report.expired_cache_entries == 1
    assert lookup_cache_entry(world.db, stale.cache_key) is None
    assert lookup_cache_entry(world.db, edge.cache_key)
    assert lookup_cache_entry(world.db, fresh.cache_key)
    assert reap().expired_cache_entries == 0
