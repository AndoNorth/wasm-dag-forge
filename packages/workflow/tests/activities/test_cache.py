"""The compile cache activities against a real store and database, as plain functions under a
`wdf_artifacts` login with the `workflow` store identity."""

import asyncio
import json
import threading
import time
import uuid

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from temporalio.exceptions import ApplicationError
from wdf_compile import bundle
from wdf_workflow.activities import Committer, CompileCache
from wdf_workflow.activities import cache as cache_module
from wdf_workflow.testing.control import (
    CacheInvalidate,
    CacheLookup,
    CacheRecord,
    Commit,
    ControlCase,
    control_activity_contract,
)

pytestmark = [pytest.mark.integration, pytest.mark.pair]

BUILD_INFO = "contracts/workload/vectors/build-info/valid/workloads.json"
APP = "def run():\n    return 1\n"
LOCK_WAIT_S = 5


@pytest.fixture
def cache(login, workflow_store):
    return CompileCache(login("wdf_artifacts"), workflow_store)


@pytest.fixture
def source(tmp_path, submit):
    """A stored bundle of app.py, with a uv.lock unless `lockfile` is false."""

    def put(tag: str, lockfile: bool = True):
        tree = tmp_path / tag / "tree"
        tree.mkdir(parents=True)
        (tree / "pyproject.toml").write_text(f'[project]\nname = "{tag}"\nversion = "0"\n')
        if lockfile:
            (tree / "uv.lock").write_text(f"version = 1\n# {tag}\n")
        (tree / "app.py").write_text(APP)
        path = bundle.write(tree, tmp_path / tag)
        return submit.put_content(path, "source", bundle.NAME)

    return put


@pytest.fixture
def built(root, role, login, workflow_store):
    """The committed component and build-info of a compile of a key, as a compile worker writes
    them and the workflow commits them, with `component_of` the tag of its distinct bytes."""
    vector = json.loads((root / BUILD_INFO).read_text())
    committer = Committer(login("wdf_artifacts"), workflow_store)

    def compile_(key: str, component_of: str, job_id: str):
        compile_role = role("compile")
        body = f"component {component_of}".encode()
        component = compile_role.put_content(body, "component", "component.wasm")
        info = {
            **vector,
            "cache_key": key,
            "component": {"sha256": component.sha256, "size": component.size},
        }
        build_info = compile_role.put_content(
            json.dumps(info).encode(), "build-info", "build-info.json"
        )
        committer.commit(
            Commit(job_id=job_id, task="build", attempt="r0a1", references=[component, build_info])
        )
        return component, build_info

    return compile_


def lookup(cache, source, job_id: str, entry: str = "app"):
    return cache.cache_lookup(CacheLookup(source=source, entry=entry, job_id=job_id))


def record(cache, key, outputs, job_id: str):
    component, build_info = outputs
    cache.cache_record(
        CacheRecord(key=key, component=component, build_info=build_info, job_id=job_id)
    )


def raised(call, request) -> ApplicationError:
    with pytest.raises(ApplicationError) as error:
        call(request)
    return error.value


def test_cache_miss_record_then_hit_links_job(cache, source, built, uses):
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    src = source("hit")
    miss = lookup(cache, src, first)
    assert miss.key is not None and (miss.component, miss.build_info) == (None, None)

    outputs = built(miss.key, "hit", first)
    record(cache, miss.key, outputs, first)
    hit = lookup(cache, src, second)
    assert (hit.key, hit.component, hit.build_info) == (miss.key, *outputs)
    assert uses(second) == sorted(reference.uri for reference in outputs)

    # The first writer of a key wins, so a second record leaves the entry as it was.
    record(cache, miss.key, built(miss.key, "late", first), first)
    assert lookup(cache, src, second).component == outputs[0]


def test_cache_key_memoised_per_source_and_entry(cache, source, dead_store):
    src = source("memo")
    key = lookup(cache, src, str(uuid.uuid4())).key

    # A miss reads the store only for the bundle, so a known source and entry needs no store.
    cache.store = dead_store
    assert lookup(cache, src, str(uuid.uuid4())).key == key
    with pytest.raises(ApplicationError):
        lookup(cache, src, str(uuid.uuid4()), entry="other")


def test_cache_error_not_miss(cache, source, built, login, workflow_store, dead_store):
    job_id = str(uuid.uuid4())
    src = source("error")
    key = lookup(cache, src, job_id).key
    record(cache, key, built(key, "error", job_id), job_id)

    # The store fails the check of the cached component.
    cache.store = dead_store
    error = raised(cache.cache_lookup, CacheLookup(source=src, entry="app", job_id=job_id))
    assert (error.type, error.non_retryable) == ("STORE_UNAVAILABLE", False)

    # The store fails the read of the bundle.
    fresh = CompileCache(login("wdf_artifacts"), dead_store)
    error = raised(fresh.cache_lookup, CacheLookup(source=src, entry="app", job_id=job_id))
    assert (error.type, error.non_retryable) == ("STORE_UNAVAILABLE", False)

    # The database is down: the error is raised, which the activity's retry policy retries.
    down = CompileCache("postgresql://nobody:none@127.0.0.1:1/appdb", workflow_store)
    with pytest.raises(psycopg.OperationalError):
        lookup(down, src, job_id)


def test_bundle_without_lockfile_has_no_key(cache, source, submit):
    job_id = str(uuid.uuid4())
    assert lookup(cache, source("unlocked", lockfile=False), job_id).key is None
    locked = source("locked")
    assert lookup(cache, locked, job_id, entry="missing").key is None
    assert lookup(cache, locked, job_id).key is not None
    not_tar = submit.put_content(b"not a tar archive", "source", bundle.NAME)
    assert lookup(cache, not_tar, job_id).key is None


def test_build_info_key_mismatch_refused(cache, source, built, submit, role):
    job_id = str(uuid.uuid4())
    src = source("mismatch")
    key = lookup(cache, src, job_id).key
    component, build_info = built("f" * 64, "mismatch", job_id)
    error = raised(
        cache.cache_record,
        CacheRecord(key=key, component=component, build_info=build_info, job_id=job_id),
    )
    assert (error.type, error.non_retryable) == ("CommitRefused", True)
    assert f"is of cache key {'f' * 64}" in str(error)

    # A build-info of the key that describes another component than the one recorded.
    _, other_info = built(key, "mismatch-other", job_id)
    error = raised(
        cache.cache_record,
        CacheRecord(key=key, component=component, build_info=other_info, job_id=job_id),
    )
    assert (error.type, error.non_retryable) == ("CommitRefused", True)
    assert "describes another component" in str(error)

    garbage = role("compile").put_content(b"{}", "build-info", "build-info.json")
    error = raised(
        cache.cache_record,
        CacheRecord(key=key, component=component, build_info=garbage, job_id=job_id),
    )
    assert (error.type, error.non_retryable) == ("ARTIFACT_CORRUPT", True)
    assert lookup(cache, src, job_id).component is None


def test_stale_invalidate_keeps_fresh_entry(cache, source, built):
    job_id = str(uuid.uuid4())
    src = source("stale")
    key = lookup(cache, src, job_id).key
    old = built(key, "stale-old", job_id)
    record(cache, key, old, job_id)

    # One job finds the cached component lost, retires the entry and records its rebuild.
    assert cache.cache_invalidate(CacheInvalidate(key=key, component=old[0])) is True
    fresh = built(key, "stale-fresh", job_id)
    record(cache, key, fresh, job_id)

    # Another job that lost the same old component finds the entry no longer names it.
    assert cache.cache_invalidate(CacheInvalidate(key=key, component=old[0])) is False
    assert lookup(cache, src, job_id).component == fresh[0]


def test_cache_lost_wasm_retired_on_lookup(cache, source, built, vanish):
    job_id = str(uuid.uuid4())
    src = source("lost")
    key = lookup(cache, src, job_id).key
    old = built(key, "lost-old", job_id)
    record(cache, key, old, job_id)
    vanish(old[0])

    # The miss retires the entry, so the rebuild's record wins the key.
    assert lookup(cache, src, job_id).component is None
    fresh = built(key, "lost-fresh", job_id)
    record(cache, key, fresh, job_id)
    assert lookup(cache, src, job_id).component == fresh[0]


def test_cache_invalidate_races_fresh_record(
    cache, source, built, login, workflow_store, db, monkeypatch
):
    job_id = str(uuid.uuid4())
    src = source("race")
    key = lookup(cache, src, job_id).key
    old = built(key, "race-old", job_id)
    fresh = built(key, "race-fresh", job_id)
    record(cache, key, old, job_id)

    rival_dsn = login("wdf_artifacts")
    rival = CompileCache(rival_dsn, workflow_store)
    rival_user = conninfo_to_dict(rival_dsn)["user"]
    answers = {}

    def rival_job():
        # Another job lost the same component: it invalidates, rebuilds and records the rebuild.
        answers["invalidated"] = rival.cache_invalidate(CacheInvalidate(key=key, component=old[0]))
        record(rival, key, fresh, job_id)

    real = cache_module.invalidate_cache_entry
    thread = threading.Thread(target=rival_job)

    def between_lookup_and_invalidation(conn, *args):
        # The entry is read and locked: the rival's invalidation must wait for this transaction.
        if thread.ident is None:
            thread.start()
            deadline = time.monotonic() + LOCK_WAIT_S
            while not db.execute(
                "SELECT 1 FROM pg_stat_activity WHERE usename = %s AND wait_event_type = 'Lock'",
                (rival_user,),
            ).fetchone():
                assert time.monotonic() < deadline, "the rival never waited for the entry's lock"
                time.sleep(0.05)
        return real(conn, *args)

    monkeypatch.setattr(cache_module, "invalidate_cache_entry", between_lookup_and_invalidation)
    assert cache.cache_invalidate(CacheInvalidate(key=key, component=old[0])) is True
    thread.join(timeout=30)

    assert answers == {"invalidated": False}
    assert lookup(cache, src, job_id).component == fresh[0]


def test_cache_activities_pass_control_contract(cache, source, built):
    job_id = str(uuid.uuid4())
    src = source("contract")
    key = lookup(cache, src, job_id).key
    component, build_info = built(key, "contract", job_id)
    other = built("f" * 64, "contract-other", job_id)
    cases = [
        ControlCase("cache_lookup", CacheLookup(source=src, entry="app", job_id=job_id)),
        ControlCase(
            "cache_record",
            CacheRecord(key=key, component=component, build_info=other[1], job_id=job_id),
            raises="CommitRefused",
        ),
        ControlCase(
            "cache_record",
            CacheRecord(key=key, component=component, build_info=build_info, job_id=job_id),
        ),
        ControlCase("cache_lookup", CacheLookup(source=src, entry="app", job_id=job_id)),
        ControlCase("cache_invalidate", CacheInvalidate(key=key, component=other[0])),
        ControlCase("cache_invalidate", CacheInvalidate(key=key, component=component)),
    ]
    impl = {
        "cache_lookup": cache.cache_lookup,
        "cache_record": cache.cache_record,
        "cache_invalidate": cache.cache_invalidate,
    }
    asyncio.run(control_activity_contract(impl, cases))
