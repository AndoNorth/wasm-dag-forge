"""The library against a real database: references, soft deletes and compile cache entries."""

import threading
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
import yaml
from wdf_artifactindex import (
    Deleted,
    Existing,
    Indexed,
    InvalidReference,
    Lost,
    Pinned,
    WasmGone,
    Won,
    index_reference,
    mark_deleted,
    record_cache_entry,
    references_to,
    touch_cache_entry,
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
