"""Commit by reference and the index-first put of a connection holder, with a store and a database.

The database login is a stand-in member of `wdf_artifacts`, the group role of the dispatch API and
of the workflow's record activity, and the purge is run by a stand-in member of `wdf_reaper`.
"""

import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from wdf_artifactclient import client as client_module
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.commit import CommitRefused, commit
from wdf_artifactclient.errors import ArtifactLost, ArtifactNotFound, OutputUploadFailed
from wdf_artifactclient.keys import split_uri
from wdf_artifactindex import Deleted, Purged, index_reference, purge, tombstone, uses_of
from wdf_events.models import ProducerRef
from wdf_storecheck.fixtures import running
from wdf_storecheck.s3 import client as s3_client

pytestmark = [pytest.mark.integration, pytest.mark.pair]

GRACE = timedelta(days=2)
INTERVAL = timedelta(hours=1)


def payload(label: str, size: int = 4096) -> bytes:
    return hashlib.shake_256(f"commit {label}".encode()).digest(size)


def producer(job: str, attempt: str, task: str = "t0") -> ProducerRef:
    return ProducerRef(job=job, task=task, attempt=attempt)


@pytest.fixture(scope="module")
def shelf(t1, tmp_path_factory):
    """One SeaweedFS container for this module, so that it is gone before other modules run."""
    with running(t1, "seaweedfs", tmp_path_factory.mktemp("shelf")) as started:
        started.store().create_buckets()
        yield started


@pytest.fixture
def role(shelf):
    """A client of a role of the contract, with no real waiting between tries."""
    policy = RetryPolicy(sleep=lambda seconds: None)
    return lambda name: ArtifactClient(shelf.endpoint, shelf.credentials[name], policy)


@pytest.fixture
def login(appdb):
    """Opens an autocommit connection as a fresh login user that is a member of the group role."""
    users: list[tuple[str, psycopg.Connection]] = []
    with psycopg.connect(appdb, autocommit=True) as admin:

        def connect(group: str) -> psycopg.Connection:
            user = f"standin_{group}_{time.time_ns()}"
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'standin' IN ROLE {}").format(
                    sql.Identifier(user), sql.Identifier(group)
                )
            )
            conn = psycopg.connect(
                make_conninfo(appdb, user=user, password="standin"), autocommit=True
            )
            users.append((user, conn))
            return conn

        yield connect
        for user, conn in users:
            conn.close()
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(user)))


@pytest.fixture
def artifacts(login):
    return login("wdf_artifacts")


@pytest.fixture
def reaper(login):
    return login("wdf_reaper")


@pytest.fixture
def deleter(shelf):
    """Removes an object from the store, as the reaper role does."""
    s3 = s3_client(shelf.endpoint, shelf.credentials["reaper"])

    def run(uri: str) -> None:
        bucket, key = split_uri(uri)
        s3.delete_object(Bucket=bucket, Key=key)

    return run


@pytest.fixture
def purger(reaper, deleter):
    """Purges a blob as the reaper does: the row under its lock, then the object."""

    def run(uri: str) -> None:
        at = datetime.now(UTC) + timedelta(days=30)
        tombstone(reaper, uri, at, GRACE)
        with reaper.transaction():
            assert isinstance(purge(reaper, uri, at + 2 * INTERVAL, INTERVAL, GRACE), Purged)
            deleter(uri)

    return run


@pytest.fixture
def job() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def tenant(request) -> str:
    return hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]


def row_of(conn, uri: str) -> tuple | None:
    return conn.execute(
        "SELECT deleted_at IS NOT NULL, purged_at IS NOT NULL FROM artifact WHERE uri = %s", (uri,)
    ).fetchone()


def object_exists(role, reference) -> bool:
    try:
        role("workflow").head(reference)
    except ArtifactNotFound:
        return False
    return True


def component(role, label: str):
    return role("compile").put_content(payload(label), "component", "component.wasm")


def put_result(role, tenant: str, job: str, attempt: str, data: bytes, task: str = "t0"):
    return role("simulate").put_attempt(
        data, "result", "results.xml", producer(job, attempt, task), tenant, "b1", 1
    )


def test_zombie_upload_changes_nothing(role, artifacts, tenant, job, tmp_path):
    first = put_result(role, tenant, job, "r0a1", b"first attempt")
    second = put_result(role, tenant, job, "r0a2", b"second attempt")

    committed = commit(artifacts, role("workflow"), [second], job, "t0", "r0a2")
    assert list(committed.row_ids) == [second.uri]

    zombie = put_result(role, tenant, job, "r0a1", b"the first attempt, again and late")
    assert zombie.uri == first.uri

    out = tmp_path / "result.xml"
    role("aggregate").get(second, out)
    assert out.read_bytes() == b"second attempt"
    named = artifacts.execute(
        "SELECT count(*) FROM artifact WHERE uri LIKE %s", (f"%/{job}/%/r0a1/%",)
    ).fetchone()
    assert named == (0,)
    assert row_of(artifacts, second.uri) == (False, False)


@pytest.mark.parametrize(
    ("attempt", "job_id", "task", "accepted"),
    [
        ("r0a1", None, "t0", "r0a2"),
        ("r0a2", str(uuid.uuid4()), "t0", "r0a2"),
        ("r0a2", None, "t1", "r0a2"),
        ("r0a2", None, "t0", None),
    ],
    ids=["another-attempt", "another-job", "another-task", "no-accepted-attempt"],
)
def test_commit_refuses_another_attempt_job_or_task(
    attempt, job_id, task, accepted, role, artifacts, tenant, job
):
    reference = put_result(role, tenant, job_id or job, attempt, b"someone else's")
    with pytest.raises(CommitRefused):
        commit(artifacts, role("workflow"), [reference], job, task, accepted)
    assert artifacts.execute("SELECT count(*) FROM artifact").fetchone() == (0,)


def test_content_addressed_commit_links_the_job(role, artifacts, job):
    wasm = component(role, "linked")
    info = role("compile").put_content(payload("info"), "build-info", "build-info.json")

    committed = commit(artifacts, role("workflow"), [info, wasm], job, "t0")

    assert set(committed.row_ids) == {wasm.uri, info.uri}
    assert committed.revived == ()
    for reference in (wasm, info):
        assert [use.job_id for use in uses_of(artifacts, reference.uri)] == [job]
    assert commit(artifacts, role("workflow"), [wasm], job, "t0").revived == ()
    assert len(uses_of(artifacts, wasm.uri)) == 1


def test_content_addressed_commit_is_shared_across_tenants_and_linked_per_job(role, artifacts, job):
    other = str(uuid.uuid4())
    wasm = component(role, "shared")
    commit(artifacts, role("workflow"), [wasm], job, "t0")
    commit(artifacts, role("workflow"), [wasm], other, "t0")
    assert {use.job_id for use in uses_of(artifacts, wasm.uri)} == {job, other}


def test_commit_keeps_tenants_apart(role, artifacts, job):
    acme = put_result(role, "acme", job, "r0a1", b"acme bytes")
    globex = put_result(role, "globex", job, "r0a1", b"globex bytes")
    assert acme.uri != globex.uri

    commit(artifacts, role("workflow"), [acme], job, "t0", "r0a1")

    assert row_of(artifacts, acme.uri) == (False, False)
    assert row_of(artifacts, globex.uri) is None


def test_commit_revives_a_tombstoned_row_when_the_object_exists(role, artifacts, reaper, job):
    wasm = component(role, "tombstoned")
    index_reference(artifacts, wasm)
    assert isinstance(
        tombstone(reaper, wasm.uri, datetime.now(UTC) + timedelta(days=30), GRACE), Deleted
    )
    assert row_of(artifacts, wasm.uri) == (True, False)

    committed = commit(artifacts, role("workflow"), [wasm], job, "t0")

    assert committed.revived == (wasm.uri,)
    assert row_of(artifacts, wasm.uri) == (False, False)
    assert [use.job_id for use in uses_of(artifacts, wasm.uri)] == [job]


def test_commit_after_the_object_was_purged_fails_retryably_and_changes_nothing(
    role, artifacts, purger, job
):
    wasm = component(role, "purged")
    index_reference(artifacts, wasm)
    purger(wasm.uri)
    assert row_of(artifacts, wasm.uri) == (True, True)

    with pytest.raises(OutputUploadFailed) as failed:
        commit(artifacts, role("workflow"), [wasm], job, "t0")

    assert failed.value.reason.startswith("OUTPUT_UPLOAD_FAILED: ")
    assert row_of(artifacts, wasm.uri) == (True, True)
    assert uses_of(artifacts, wasm.uri) == []

    assert component(role, "purged") == wasm
    assert commit(artifacts, role("workflow"), [wasm], job, "t0").revived == (wasm.uri,)
    assert row_of(artifacts, wasm.uri) == (False, False)


def test_commit_of_a_missing_result_rolls_back_the_whole_commit(
    role, artifacts, deleter, tenant, job
):
    gone = put_result(role, tenant, job, "r0a1", b"lost", "t9")
    deleter(gone.uri)
    wasm = component(role, "rolled-back")

    with pytest.raises(OutputUploadFailed):
        commit(artifacts, role("workflow"), [wasm, gone], job, "t9", "r0a1")

    assert artifacts.execute("SELECT count(*) FROM artifact").fetchone() == (0,)
    assert uses_of(artifacts, wasm.uri) == []


def test_reused_attempt_is_caught_by_the_hash(role, tenant, job, tmp_path):
    reference = put_result(role, tenant, job, "r0a1", b"the committed bytes")
    put_result(role, tenant, job, "r0a1", b"a different overwrite")

    with pytest.raises(ArtifactLost) as lost:
        role("aggregate").get(reference, tmp_path / "out.bin")
    assert lost.value.cause.code == "ARTIFACT_CORRUPT"


def test_put_content_with_a_connection_requires_a_job_id(role, artifacts, job):
    with pytest.raises(ValueError, match="job_id"):
        role("submit").put_content(b"{}", "manifest", "manifest.json", conn=artifacts)
    with pytest.raises(ValueError, match="job_id"):
        role("submit").put_content(b"{}", "manifest", "manifest.json", job_id=job)
    assert artifacts.execute("SELECT count(*) FROM artifact").fetchone() == (0,)


def test_put_content_links_the_job_after_the_bytes(role, artifacts, job, monkeypatch):
    submit = role("submit")
    data = payload("manifest")
    sha256 = hashlib.sha256(data).hexdigest()
    uri = f"s3://wdf-sources/sha256/{sha256[:2]}/{sha256}"
    seen = []
    send = submit._upload

    def watched(*args, **kwargs):
        seen.append((row_of(artifacts, uri), uses_of(artifacts, uri)))
        return send(*args, **kwargs)

    monkeypatch.setattr(submit, "_upload", watched)

    reference = submit.put_content(data, "manifest", "manifest.json", conn=artifacts, job_id=job)

    assert reference.uri == uri
    assert seen == [((False, False), [])]
    assert [use.job_id for use in uses_of(artifacts, uri)] == [job]
    assert object_exists(role, reference)


# The purge of the setup is the one before the index, so that case runs no purge of its own.
PURGE_AT = ["before-the-index", "between-the-index-and-the-put", "between-the-put-and-the-link"]


@pytest.mark.parametrize("at", PURGE_AT)
def test_revive_before_put_never_leaves_a_live_row_without_an_object(
    at, role, artifacts, purger, job, monkeypatch
):
    submit = role("submit")
    first = submit.put_content(payload(at), "manifest", "manifest.json")
    index_reference(artifacts, first)
    purger(first.uri)
    assert row_of(artifacts, first.uri) == (True, True)
    assert not object_exists(role, first)
    hits = []

    def purge_once() -> None:
        if not hits:
            hits.append(at)
            purger(first.uri)

    send, link = submit._upload, client_module.link_use

    def upload(*args, **kwargs):
        if at == "between-the-index-and-the-put":
            purge_once()
        return send(*args, **kwargs)

    def linked(*args, **kwargs):
        if at == "between-the-put-and-the-link":
            purge_once()
        return link(*args, **kwargs)

    monkeypatch.setattr(submit, "_upload", upload)
    monkeypatch.setattr(client_module, "link_use", linked)

    again = submit.put_content(payload(at), "manifest", "manifest.json", conn=artifacts, job_id=job)

    assert again == first
    assert hits == ([] if at == "before-the-index" else [at])
    assert row_of(artifacts, first.uri) == (False, False)
    assert object_exists(role, again)
    assert [use.job_id for use in uses_of(artifacts, first.uri)] == [job]


def test_putting_before_indexing_would_leave_a_live_row_without_an_object(role, artifacts, purger):
    """The control of the test above: with the steps the other way round the rule breaks."""
    first = role("submit").put_content(payload("reverse"), "manifest", "manifest.json")
    index_reference(artifacts, first)
    role("submit").put_content(payload("reverse"), "manifest", "manifest.json")
    purger(first.uri)
    index_reference(artifacts, first)

    assert row_of(artifacts, first.uri) == (False, False)
    assert not object_exists(role, first)
