"""The library against a real database: references, use links, soft deletes and cache entries."""

import hashlib
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
import yaml
from wdf_artifactindex import (
    Deleted,
    Existing,
    Indexed,
    Invalidated,
    InvalidReference,
    Linked,
    Lost,
    NoEntry,
    Pinned,
    Purged,
    Revived,
    RowPurged,
    Skipped,
    WasmGone,
    Won,
    checkpoints_of,
    expired_cache_entries,
    index_reference,
    invalidate_cache_entry,
    link_use,
    lookup_cache_entry,
    mark_deleted,
    previous_checkpoint,
    purge,
    record_cache_entry,
    references_to,
    tombstone,
    touch_cache_entry,
    unreferenced_content,
    uses_of,
)

pytestmark = pytest.mark.integration

ARTIFACT_VECTORS = Path(__file__).resolve().parents[3] / "contracts/artifact/vectors"

NOW = datetime(2026, 11, 2, tzinfo=UTC)
LATER = datetime(2026, 11, 3, tzinfo=UTC)
JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def vectors(kind: str) -> list[Path]:
    return sorted((ARTIFACT_VECTORS / kind).rglob("*.yaml"))


def component(n: int = 0) -> dict:
    sha = f"{n:064x}"
    return {
        "uri": f"s3://wdf-components/sha256/{sha[:2]}/{sha}",
        "sha256": sha,
        "size": 10,
        "name": "job.wasm",
        "kind": "component",
    }


def result(attempt: str, task: str = "simulate-0") -> dict:
    return {
        "uri": f"s3://wdf-results/acme/b/{JOB}/seed-7/{task}/{attempt}/result.xml",
        "sha256": "4" * 64,
        "size": 1,
        "name": "result.xml",
        "kind": "result",
        "producer": {"job": JOB, "task": task, "attempt": attempt},
    }


def entry(wasm: dict, key: str = "c" * 64, **extra) -> dict:
    return {
        "cache_key": key,
        "wasm_uri": wasm["uri"],
        "componentize_py_version": "1.0",
        "wit_version": "2.0",
    } | extra


def rows(conn, where: str = "true") -> list[tuple]:
    return conn.execute(f"SELECT * FROM artifact WHERE {where} ORDER BY row_id").fetchall()


def test_every_valid_artifact_vector_is_indexed(conn):
    paths = vectors("valid")
    assert paths
    for path in paths:
        reference = load(path)
        outcome = index_reference(conn, reference)
        assert isinstance(outcome, Indexed), path.name
        stored = conn.execute(
            "SELECT kind, uri, bucket || '/' || key, sha256, size, name,"
            " producer_job::text, producer_task, producer_attempt FROM artifact WHERE row_id = %s",
            (outcome.row_id,),
        ).fetchone()
        producer = reference.get("producer", {})
        assert stored == (
            reference["kind"],
            reference["uri"],
            reference["uri"].removeprefix("s3://"),
            reference["sha256"],
            reference["size"],
            reference["name"],
            producer.get("job"),
            producer.get("task"),
            producer.get("attempt"),
        ), path.name
        assert index_reference(conn, reference) == Existing(outcome.row_id)
    assert len(rows(conn)) == len(paths)


def test_every_invalid_artifact_vector_is_refused(conn):
    paths = vectors("invalid")
    assert len(paths) >= 10
    for path in paths:
        with pytest.raises(InvalidReference):
            index_reference(conn, load(path))
    assert rows(conn) == []


def test_a_refusal_names_the_rule_of_the_loader():
    reasons = {
        "wrong-bucket": "wrong-bucket",
        "bad-hash-prefix": "bad-hash-prefix",
        "hash-mismatch": "hash-mismatch",
        "missing-attempt-segment": "missing-attempt-segment",
        "key-template-mismatch": "key-template-mismatch",
    }
    from wdf_artifactindex.reference import validate

    for path in vectors("invalid"):
        want = reasons.get(path.parent.name)
        if want:
            with pytest.raises(InvalidReference, match=f"^{want}:"):
                validate(load(path))


def test_attempt_identity_is_stored_as_given(conn):
    first, second = index_reference(conn, result("r0a1")), index_reference(conn, result("r0a2"))
    odd = index_reference(conn, result("r7a9", task="simulate-1"))
    assert all(isinstance(o, Indexed) for o in (first, second, odd))
    stored = conn.execute(
        "SELECT producer_task, producer_attempt FROM artifact ORDER BY row_id"
    ).fetchall()
    assert stored == [("simulate-0", "r0a1"), ("simulate-0", "r0a2"), ("simulate-1", "r7a9")]


def test_first_writer_wins_on_a_cache_key(conn):
    one, two = component(1), component(2)
    for wasm in (one, two):
        index_reference(conn, wasm)
    assert isinstance(record_cache_entry(conn, entry(one)), Won)
    assert record_cache_entry(conn, entry(two)) == Lost()
    assert conn.execute(
        "SELECT wasm_uri FROM artifact WHERE cache_key = %s", ("c" * 64,)
    ).fetchall() == [(one["uri"],)]


def test_first_writer_wins_when_the_entries_race(appdb, conn):
    one, two = component(1), component(2)
    for wasm in (one, two):
        index_reference(conn, wasm)
    conn.commit()
    outcomes = []

    def write(wasm):
        with psycopg.connect(appdb) as other:
            outcomes.append(record_cache_entry(other, entry(wasm)))
            other.commit()

    threads = [threading.Thread(target=write, args=(w,)) for w in (one, two)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(type(o).__name__ for o in outcomes) == ["Lost", "Won"]


def test_mark_deleted_keeps_the_row(conn):
    ref = result("r0a1")
    index_reference(conn, ref)
    assert isinstance(mark_deleted(conn, ref["uri"], NOW), Deleted)
    assert mark_deleted(conn, ref["uri"], LATER) == Deleted(rows(conn)[0][0])
    assert conn.execute("SELECT deleted_at FROM artifact").fetchall() == [(NOW,)]
    assert index_reference(conn, ref) == Revived(rows(conn)[0][0])
    assert index_reference(conn, ref) == Existing(rows(conn)[0][0])
    with pytest.raises(LookupError):
        mark_deleted(conn, "s3://wdf-results/none", NOW)


def test_mark_deleted_is_refused_while_a_cache_entry_names_the_wasm(conn):
    wasm = component(1)
    index_reference(conn, wasm)
    record_cache_entry(conn, entry(wasm))
    assert mark_deleted(conn, wasm["uri"], NOW) == Pinned()
    assert conn.execute(
        "SELECT deleted_at FROM artifact WHERE uri = %s", (wasm["uri"],)
    ).fetchone() == (None,)
    assert references_to(conn, wasm["uri"]) == ["c" * 64]


def test_a_wasm_with_no_cache_entry_has_no_references(conn):
    wasm = component(1)
    index_reference(conn, wasm)
    assert references_to(conn, wasm["uri"]) == []
    assert isinstance(mark_deleted(conn, wasm["uri"], NOW), Deleted)


def test_record_cache_entry_refuses_a_soft_deleted_wasm(conn):
    wasm = component(1)
    index_reference(conn, wasm)
    mark_deleted(conn, wasm["uri"], NOW)
    assert record_cache_entry(conn, entry(wasm)) == WasmGone()
    assert record_cache_entry(conn, entry(component(9))) == WasmGone()
    assert rows(conn, "kind = 'compile-cache'") == []


def test_touch_moves_the_last_use_forward_only(conn):
    wasm = component(1)
    index_reference(conn, wasm)
    record_cache_entry(conn, entry(wasm, last_used_at=NOW))
    touch_cache_entry(conn, "c" * 64, LATER)
    touch_cache_entry(conn, "c" * 64, NOW)
    assert conn.execute(
        "SELECT last_used_at FROM artifact WHERE kind = 'compile-cache'"
    ).fetchone() == (LATER,)


def pinned_and_deleted(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM artifact w JOIN artifact c ON c.wasm_uri = w.uri"
        " WHERE w.deleted_at IS NOT NULL AND c.deleted_at IS NULL"
    ).fetchone()[0]


def blocked(admin, timeout: float = 10) -> None:
    """Waits until another session of the database waits for a lock."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if admin.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
            " AND datname = current_database()"
        ).fetchone()[0]:
            return
        time.sleep(0.05)
    raise AssertionError("no session is waiting for a lock")


def in_thread(dsn, fn):
    box = {}

    def run():
        with psycopg.connect(dsn) as other:
            box["value"] = fn(other)
            other.commit()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


@pytest.fixture
def wasm(appdb, conn):
    ref = component(1)
    index_reference(conn, ref)
    conn.commit()
    return ref


def test_concurrent_mark_deleted_and_record_cache_entry_leave_no_pinned_deleted_wasm(
    appdb, conn, wasm
):
    # The entry is written first and uncommitted: the soft delete waits, then sees it and refuses.
    with psycopg.connect(appdb) as writer, psycopg.connect(appdb, autocommit=True) as admin:
        assert isinstance(record_cache_entry(writer, entry(wasm)), Won)
        thread, box = in_thread(appdb, lambda c: mark_deleted(c, wasm["uri"], NOW))
        blocked(admin)
        writer.commit()
        thread.join(10)
        assert box["value"] == Pinned()
    assert pinned_and_deleted(conn) == 0

    # The soft delete is first and uncommitted: the entry waits, then sees the delete and refuses.
    other = component(2)
    index_reference(conn, other)
    conn.commit()
    with psycopg.connect(appdb) as deleter, psycopg.connect(appdb, autocommit=True) as admin:
        assert isinstance(mark_deleted(deleter, other["uri"], NOW), Deleted)
        thread, box = in_thread(appdb, lambda c: record_cache_entry(c, entry(other, key="d" * 64)))
        blocked(admin)
        deleter.commit()
        thread.join(10)
        assert box["value"] == WasmGone()
    assert pinned_and_deleted(conn) == 0
    assert rows(conn, "cache_key = '%s'" % ("d" * 64)) == []


def race(dsn, barrier, fn, *args):
    def run(c):
        barrier.wait()
        return fn(c, *args)

    return in_thread(dsn, run)


def test_racing_mark_deleted_and_record_cache_entry_never_leave_a_pinned_deleted_wasm(appdb, conn):
    for n in range(10, 40):
        ref = component(n)
        index_reference(conn, ref)
        conn.commit()
        barrier = threading.Barrier(2)
        threads = [
            race(appdb, barrier, mark_deleted, ref["uri"], NOW),
            race(appdb, barrier, record_cache_entry, entry(ref, key=f"{n:064x}")),
        ]
        for thread, _ in threads:
            thread.join(10)
        assert pinned_and_deleted(conn) == 0, n


START = datetime(2026, 11, 2, tzinfo=UTC)
GRACE = timedelta(days=2)
INTERVAL = timedelta(hours=1)
KEY = "c" * 64
JOB_A = "3a9617fa-df7c-5760-a644-42d559e0f433"
JOB_B = "7c1d2f10-0000-5000-8000-000000000001"


def manifest(n: int = 0) -> dict:
    sha = f"{n + 5000:064x}"
    return {
        "uri": f"s3://wdf-sources/sha256/{sha[:2]}/{sha}",
        "sha256": sha,
        "size": 10,
        "name": "manifest.json",
        "kind": "manifest",
    }


def build_info(n: int = 0) -> dict:
    sha = f"{n + 9000:064x}"
    return {
        "uri": f"s3://wdf-components/sha256/{sha[:2]}/{sha}",
        "sha256": sha,
        "size": 10,
        "name": "build-info.json",
        "kind": "build-info",
    }


def checkpoint(attempt: str, segment: str, task: str = "simulate-0") -> dict:
    key = f"acme/b/{JOB}/seed-7/{task}/{attempt}/{segment}/checkpoint.json"
    return {
        "uri": f"s3://wdf-checkpoints/{key}",
        "sha256": "5" * 64,
        "size": 1,
        "name": "checkpoint.json",
        "kind": "checkpoint",
        "producer": {"job": JOB, "task": task, "attempt": attempt},
    }


def add_job(conn, job_id: str, status: str, manifest_ref: dict, patch: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO accepted_job (job_id, tenant, submission_key, batch_id, priority_class,"
        " status, manifest_ref, schema_version, traceparent, seed, parameters, fingerprint,"
        " pending_patch_ref) VALUES (%s, 'acme', %s, %s, 'normal', %s, %s, 'v1', 'tp', 1, '{}',"
        " %s, %s)",
        (
            job_id,
            job_id,
            job_id,
            status,
            json.dumps(manifest_ref),
            "f" * 64,
            patch and json.dumps(patch),
        ),
    )


def set_status(conn, job_id: str, status: str) -> None:
    conn.execute("UPDATE accepted_job SET status = %s WHERE job_id = %s", (status, job_id))


def age(conn, uri: str, days: int = 30) -> None:
    conn.execute(
        "UPDATE artifact SET created_at = %s WHERE uri = %s", (START - timedelta(days=days), uri)
    )


def freed(conn, at: datetime = START) -> list[str]:
    return sorted(u.uri for u in unreferenced_content(conn, at, GRACE))


def indexed(conn, ref: dict) -> None:
    assert isinstance(index_reference(conn, ref), Indexed)
    age(conn, ref["uri"])


def test_unreferenced_content_respects_live_jobs(conn):
    held, patch, free = manifest(0), manifest(1), manifest(2)
    for ref in (held, patch, free):
        indexed(conn, ref)
    add_job(conn, JOB_A, "running", held, patch)
    assert freed(conn) == [free["uri"]]
    set_status(conn, JOB_A, "succeeded")
    assert freed(conn) == sorted(r["uri"] for r in (held, patch, free))


def test_paused_job_holds_its_inputs(conn):
    source, wasm = manifest(0), component(1)
    for ref in (source, wasm):
        indexed(conn, ref)
        link_use(conn, JOB_A, ref["uri"], START)
    add_job(conn, JOB_A, "paused", source)
    assert freed(conn, START + timedelta(days=60)) == []
    set_status(conn, JOB_A, "cancelled")
    assert freed(conn, START + timedelta(days=60)) == sorted([source["uri"], wasm["uri"]])


def test_a_row_younger_than_the_grace_is_not_unreferenced(conn):
    ref = manifest(0)
    index_reference(conn, ref)
    now = conn.execute("SELECT now()").fetchone()[0]
    assert freed(conn, now) == []
    assert freed(conn, now + GRACE + timedelta(seconds=1)) == [ref["uri"]]


def test_unreferenced_content_names_one_row_and_never_a_reference_of_an_attempt(conn):
    one, two = manifest(0), manifest(1)
    for ref in (one, two):
        indexed(conn, ref)
    indexed(conn, result("r0a1"))
    assert [u.uri for u in unreferenced_content(conn, START, GRACE, two["uri"])] == [two["uri"]]
    assert len(unreferenced_content(conn, START, GRACE, limit=1)) == 1
    assert len(unreferenced_content(conn, START, GRACE)) == 2


def test_cache_entry_pins_wasm_and_build_info(conn):
    wasm, info = component(1), build_info(1)
    for ref in (wasm, info):
        indexed(conn, ref)
    assert isinstance(record_cache_entry(conn, entry(wasm, build_info_uri=info["uri"])), Won)
    assert mark_deleted(conn, wasm["uri"], START) == Pinned()
    assert mark_deleted(conn, info["uri"], START) == Pinned()
    assert references_to(conn, info["uri"]) == [KEY]
    assert freed(conn) == []
    assert isinstance(invalidate_cache_entry(conn, KEY, START), Invalidated)
    assert references_to(conn, info["uri"]) == []
    assert freed(conn) == sorted([wasm["uri"], info["uri"]])
    assert isinstance(mark_deleted(conn, info["uri"], START), Deleted)


def test_a_cache_entry_needs_a_live_build_info(conn):
    wasm, info = component(1), build_info(1)
    for ref in (wasm, info):
        indexed(conn, ref)
    mark_deleted(conn, info["uri"], START)
    assert record_cache_entry(conn, entry(wasm, build_info_uri=info["uri"])) == WasmGone()
    assert record_cache_entry(conn, entry(wasm, build_info_uri=build_info(7)["uri"])) == WasmGone()
    assert isinstance(record_cache_entry(conn, entry(wasm)), Won)


def test_record_again_after_invalidation_and_after_expiry(conn):
    wasm, info = component(1), build_info(1)
    for ref in (wasm, info):
        indexed(conn, ref)
    first = record_cache_entry(conn, entry(wasm, build_info_uri=info["uri"], last_used_at=START))
    assert isinstance(first, Won)
    assert record_cache_entry(conn, entry(wasm)) == Lost()
    assert invalidate_cache_entry(conn, KEY, START) == Invalidated(first.row_id)
    assert invalidate_cache_entry(conn, KEY, START) == NoEntry()
    assert lookup_cache_entry(conn, KEY) is None

    second = record_cache_entry(conn, entry(wasm, build_info_uri=info["uri"], last_used_at=START))
    assert isinstance(second, Won) and second.row_id != first.row_id
    retired = hashlib.sha256(f"{KEY}:{first.row_id}".encode()).hexdigest()
    old = rows(conn, f"row_id = {first.row_id}")[0]
    assert conn.execute(
        "SELECT cache_key, wasm_uri, build_info_uri, deleted_at FROM artifact WHERE row_id = %s",
        (first.row_id,),
    ).fetchone() == (retired, wasm["uri"], info["uri"], START)
    assert old[0] == first.row_id
    assert lookup_cache_entry(conn, KEY).wasm_uri == wasm["uri"]

    assert expired_cache_entries(conn, START - timedelta(days=1), START) == []
    assert expired_cache_entries(conn, START + timedelta(days=14), START) == [KEY]
    assert expired_cache_entries(conn, START + timedelta(days=14), START) == []
    third = record_cache_entry(conn, entry(wasm, build_info_uri=info["uri"]))
    assert isinstance(third, Won) and third.row_id not in (first.row_id, second.row_id)
    assert conn.execute(
        "SELECT cache_key ~ '^[0-9a-f]{64}$', count(*) FROM artifact"
        " WHERE kind = 'compile-cache' GROUP BY 1"
    ).fetchall() == [(True, 3)]


def test_a_retired_key_never_matches_a_touch_or_a_lookup(conn):
    wasm = component(1)
    indexed(conn, wasm)
    won = record_cache_entry(conn, entry(wasm, last_used_at=START))
    invalidate_cache_entry(conn, KEY, START)
    touch_cache_entry(conn, KEY, START + timedelta(days=5))
    assert conn.execute(
        "SELECT last_used_at FROM artifact WHERE row_id = %s", (won.row_id,)
    ).fetchone() == (START,)
    assert lookup_cache_entry(conn, KEY) is None


def test_expiry_stops_at_the_limit_and_leaves_the_rest_for_the_next_run(conn):
    wasm = component(1)
    indexed(conn, wasm)
    for n in range(3):
        record_cache_entry(conn, entry(wasm, key=f"{n + 1:064x}", last_used_at=START))
    cutoff = START + timedelta(days=1)
    assert len(expired_cache_entries(conn, cutoff, cutoff, limit=2)) == 2
    assert len(expired_cache_entries(conn, cutoff, cutoff, limit=2)) == 1


def test_link_use_is_idempotent_and_lists_the_use(conn):
    ref = manifest(0)
    indexed(conn, ref)
    row_id = rows(conn)[0][0]
    assert link_use(conn, JOB_A, ref["uri"], START) == Linked(row_id)
    assert link_use(conn, JOB_A, ref["uri"], START) == Linked(row_id)
    link_use(conn, JOB_B, ref["uri"], START + timedelta(hours=1))
    uses = uses_of(conn, ref["uri"])
    assert [(u.job_id, u.artifact_row_id) for u in uses] == [(JOB_A, row_id), (JOB_B, row_id)]
    assert uses_of(conn, manifest(9)["uri"]) == []
    with pytest.raises(LookupError):
        link_use(conn, JOB_A, manifest(9)["uri"], START)


def test_link_after_tombstone_revives_the_row(conn):
    ref = manifest(0)
    indexed(conn, ref)
    assert isinstance(tombstone(conn, ref["uri"], START, GRACE), Deleted)
    assert conn.execute("SELECT deleted_at FROM artifact").fetchone() == (START,)
    assert link_use(conn, JOB_A, ref["uri"], START) == Revived(rows(conn)[0][0])
    assert conn.execute("SELECT deleted_at, purged_at FROM artifact").fetchone() == (None, None)
    assert freed(conn, START + timedelta(days=1)) == []


def test_tombstone_skips_a_row_that_something_holds(conn):
    ref = manifest(0)
    indexed(conn, ref)
    link_use(conn, JOB_A, ref["uri"], START)
    assert tombstone(conn, ref["uri"], START, GRACE) == Skipped()
    assert conn.execute("SELECT deleted_at FROM artifact").fetchone() == (None,)


def tombstoned(conn, ref: dict) -> None:
    indexed(conn, ref)
    assert isinstance(tombstone(conn, ref["uri"], START, GRACE), Deleted)


def test_purge_waits_for_the_interval_and_for_a_tombstone(conn):
    live, ref = manifest(0), manifest(1)
    indexed(conn, live)
    tombstoned(conn, ref)
    assert purge(conn, live["uri"], START + timedelta(days=9), INTERVAL, GRACE) == Skipped()
    assert purge(conn, ref["uri"], START + INTERVAL - timedelta(seconds=1), INTERVAL, GRACE) == (
        Skipped()
    )
    assert purge(conn, ref["uri"], START + INTERVAL, INTERVAL, GRACE) == Purged()
    assert conn.execute(
        "SELECT purged_at FROM artifact WHERE uri = %s", (ref["uri"],)
    ).fetchone() == (START + INTERVAL,)
    assert purge(conn, ref["uri"], START + INTERVAL, INTERVAL, GRACE) == Skipped()


def test_link_of_a_purged_row_is_refused(conn):
    ref = manifest(0)
    tombstoned(conn, ref)
    assert purge(conn, ref["uri"], START + INTERVAL, INTERVAL, GRACE) == Purged()
    assert link_use(conn, JOB_A, ref["uri"], START + INTERVAL) == RowPurged()
    assert uses_of(conn, ref["uri"]) == []
    assert conn.execute(
        "SELECT deleted_at IS NOT NULL, purged_at IS NOT NULL FROM artifact"
    ).fetchone() == (True, True)


def test_index_reference_revives_a_purged_row(conn):
    ref = manifest(0)
    tombstoned(conn, ref)
    purge(conn, ref["uri"], START + INTERVAL, INTERVAL, GRACE)
    row_id = rows(conn)[0][0]
    assert index_reference(conn, ref) == Revived(row_id)
    assert conn.execute("SELECT deleted_at, purged_at FROM artifact").fetchone() == (None, None)
    assert index_reference(conn, ref) == Existing(row_id)
    assert isinstance(link_use(conn, JOB_A, ref["uri"], START), Linked)


def test_index_reference_revives_a_soft_deleted_row(conn):
    ref = result("r0a1")
    index_reference(conn, ref)
    mark_deleted(conn, ref["uri"], START)
    assert isinstance(index_reference(conn, ref), Revived)
    assert conn.execute("SELECT deleted_at FROM artifact").fetchone() == (None,)


def test_purge_evaluates_references_again_under_the_lock(appdb, conn):
    held, free = manifest(0), manifest(1)
    for ref in (held, free):
        tombstoned(conn, ref)
    conn.commit()
    at = START + INTERVAL
    # A link that holds the row lock and has not committed: the purge waits, then sees the use.
    with psycopg.connect(appdb) as linker, psycopg.connect(appdb, autocommit=True) as admin:
        assert isinstance(link_use(linker, JOB_A, held["uri"], at), Revived)
        thread, box = in_thread(appdb, lambda c: purge(c, held["uri"], at, INTERVAL, GRACE))
        blocked(admin)
        linker.commit()
        thread.join(10)
        assert box["value"] == Skipped()
    assert conn.execute(
        "SELECT deleted_at, purged_at FROM artifact WHERE uri = %s", (held["uri"],)
    ).fetchone() == (None, None)
    # A purge that holds the row lock and has not committed: the link waits, then is refused.
    with psycopg.connect(appdb) as purger, psycopg.connect(appdb, autocommit=True) as admin:
        assert purge(purger, free["uri"], at, INTERVAL, GRACE) == Purged()
        thread, box = in_thread(appdb, lambda c: link_use(c, JOB_B, free["uri"], at))
        blocked(admin)
        purger.commit()
        thread.join(10)
        assert box["value"] == RowPurged()


def test_racing_links_and_purges_never_leave_a_held_blob_purged(appdb, conn):
    at = START + INTERVAL
    for n in range(10, 30):
        ref = manifest(n)
        tombstoned(conn, ref)
        conn.commit()
        barrier = threading.Barrier(2)

        threads = [
            race(appdb, barrier, link_use, JOB_A, ref["uri"], at),
            race(appdb, barrier, purge, ref["uri"], at, INTERVAL, GRACE),
        ]
        for thread, _ in threads:
            thread.join(10)
        linked, purged = threads[0][1]["value"], threads[1][1]["value"]
        assert (isinstance(linked, RowPurged), isinstance(purged, Purged)) in (
            (True, True),
            (False, False),
        ), n
        held = conn.execute(
            "SELECT count(*) FROM artifact a JOIN artifact_use u ON u.artifact_row_id = a.row_id"
            " WHERE a.purged_at IS NOT NULL"
        ).fetchone()[0]
        assert held == 0, n


def test_use_row_of_a_job_without_a_row_protects_the_blob_until_the_grace(conn):
    ref = manifest(0)
    indexed(conn, ref)
    link_use(conn, JOB_A, ref["uri"], START)
    assert freed(conn, START + timedelta(days=1)) == []
    assert freed(conn, START + GRACE) == [ref["uri"]]
    add_job(conn, JOB_A, "accepted", manifest(8))
    assert freed(conn, START + timedelta(days=30)) == []
    set_status(conn, JOB_A, "cancelled")
    assert freed(conn, START + timedelta(days=30)) == [ref["uri"]]


def test_retried_link_renews_a_stale_use_row(conn):
    ref = manifest(0)
    indexed(conn, ref)
    link_use(conn, JOB_A, ref["uri"], START)
    assert freed(conn, START + timedelta(days=5)) == [ref["uri"]]
    link_use(conn, JOB_A, ref["uri"], START + timedelta(days=5))
    assert freed(conn, START + timedelta(days=6)) == []
    link_use(conn, JOB_A, ref["uri"], START)
    (use,) = uses_of(conn, ref["uri"])
    assert (use.created_at, use.last_linked_at) == (START, START + timedelta(days=5))


def test_checkpoints_of_lists_the_live_checkpoints_of_a_task_newest_first(conn):
    refs = [checkpoint("r0a1", "s1"), checkpoint("r0a1", "s2"), checkpoint("r0a2", "s3")]
    for ref in refs:
        index_reference(conn, ref)
    index_reference(conn, checkpoint("r0a1", "s1", task="simulate-1"))
    other = [c.uri for c in checkpoints_of(conn, JOB, "simulate-1")]
    assert len(other) == 1
    assert [c.uri for c in checkpoints_of(conn, JOB, "simulate-0")] == [
        r["uri"] for r in reversed(refs)
    ]
    assert previous_checkpoint(conn, refs[2]["uri"]).uri == refs[1]["uri"]
    assert previous_checkpoint(conn, refs[0]["uri"]) is None
    mark_deleted(conn, refs[1]["uri"], START)
    assert previous_checkpoint(conn, refs[2]["uri"]).uri == refs[0]["uri"]
    assert previous_checkpoint(conn, other[0]) is None


def test_wdf_migrate_check_passes_against_every_applied_migration(appdb):
    done = subprocess.run(
        [sys.executable, "-m", "wdf_migrate", "check", "--dsn", appdb],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
