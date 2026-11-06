"""A store and a database for the reaper tests, and a small world of jobs and objects in them.

The reaper's database login is a stand-in member of `wdf_reaper`, and the store clients are the
credentials of the roles of the contract. The store and the database are the two containers that a
session holds, each for the length of a test module.
"""

import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from wdf_artifactclient import cache as cache_module
from wdf_artifactclient import client as client_module
from wdf_artifactclient import commit as commit_module
from wdf_artifactclient.checkpoint import write_checkpoint
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactindex import index_reference
from wdf_artifactreaper.content import CONTENT_BUCKETS
from wdf_artifactreaper.reap import load_windows, reap_once
from wdf_events.models import ProducerRef
from wdf_jobstate import JobRow, insert_accepted
from wdf_storecheck.fixtures import running
from wdf_storecheck.s3 import client as s3_client

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
WINDOWS = load_windows()
SHA = "a" * 64
TRACE = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
WASM = hashlib.sha256(b"component").hexdigest()
META = {"format_version": 1, "progress": 0, "wasm_sha256": WASM}


@pytest.fixture(scope="module")
def shelf(t1, tmp_path_factory):
    """One SeaweedFS container for this module, which is gone before other modules run.

    A session fixture would outlive the package and add a container to the count that the
    container tests of other packages assert on.
    """
    with running(t1, "seaweedfs", tmp_path_factory.mktemp("shelf")) as started:
        started.store().create_buckets()
        yield started


@pytest.fixture
def db(appdb):
    """The superuser, for building the state that the reaper then judges."""
    with psycopg.connect(appdb, autocommit=True) as connection:
        yield connection


@pytest.fixture
def login(appdb, db):
    """Opens an autocommit connection as a fresh login user that is a member of a group role."""
    users: list[tuple[str, psycopg.Connection]] = []

    def dsn(group: str) -> str:
        user = f"standin_{group}_{time.time_ns()}"
        db.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'standin' IN ROLE {}").format(
                sql.Identifier(user), sql.Identifier(group)
            )
        )
        return make_conninfo(appdb, user=user, password="standin")

    def connect(group: str) -> psycopg.Connection:
        conn = psycopg.connect(dsn(group), autocommit=True)
        users.append((conn.info.user, conn))
        return conn

    connect.dsn = dsn
    yield connect
    for user, conn in users:
        conn.close()
        db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(user)))
        db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(user)))


@pytest.fixture
def reaper(login):
    return login("wdf_reaper")


class Counting:
    """The reaper's store client, counting what it asks and able to fail a delete once."""

    def __init__(self, s3):
        self._s3 = s3
        self.lists = 0
        self.fail_next_delete = False

    def list_objects_v2(self, **kwargs):
        # The sweep of the content-addressed buckets lists them on every run.
        self.lists += kwargs["Bucket"] not in CONTENT_BUCKETS
        return self._s3.list_objects_v2(**kwargs)

    def delete_object(self, **kwargs):
        return self._s3.delete_object(**kwargs)

    def delete_objects(self, **kwargs):
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise ConnectionError("the store went away")
        return self._s3.delete_objects(**kwargs)


@pytest.fixture
def store(shelf):
    return Counting(s3_client(shelf.endpoint, shelf.credentials["reaper"]))


class World:
    """Jobs, results and checkpoints that exist both in the database and in the store."""

    def __init__(self, shelf, db, tenant: str):
        self.db = db
        self.tenant = tenant
        self.admin = s3_client(shelf.endpoint, shelf.credentials["admin"])
        policy = RetryPolicy(sleep=lambda seconds: None)
        self.worker = ArtifactClient(shelf.endpoint, shelf.credentials["simulate"], policy)
        self.aggregate = ArtifactClient(shelf.endpoint, shelf.credentials["aggregate"], policy)
        self.submit = ArtifactClient(shelf.endpoint, shelf.credentials["submit"], policy)
        self.compile = ArtifactClient(shelf.endpoint, shelf.credentials["compile"], policy)
        self.indexer = ArtifactClient(shelf.endpoint, shelf.credentials["indexer"], policy)
        self.workflow = ArtifactClient(shelf.endpoint, shelf.credentials["workflow"], policy)
        self.batch = str(uuid.uuid4())
        self.label = tenant

    def payload(self, label: str) -> bytes:
        """Bytes that no other test puts, so that a content-addressed key is the test's own."""
        return f"{self.label} {label}".encode()

    def clear_content(self) -> None:
        """Empties the content-addressed buckets, which tests of the session share."""
        for bucket in CONTENT_BUCKETS:
            for key in self.keys(bucket, ""):
                self.admin.delete_object(Bucket=bucket, Key=key)

    def job(
        self,
        status: str = "succeeded",
        terminal_at: datetime | None = None,
        *,
        job_id: str | None = None,
        manifest: dict | None = None,
    ) -> str:
        """A job at the status, with the terminal clock set directly because the guard of the
        row only allows the moves of the lifecycle. `manifest` is the reference it names."""
        job_id = job_id or str(uuid.uuid4())
        row = JobRow(
            job_id=job_id,
            tenant=self.tenant,
            submission_key=f"key-{job_id}",
            batch_id=self.batch,
            priority_class="standard",
            manifest_ref=manifest
            or {
                "uri": f"s3://wdf-sources/sha256/aa/{SHA}",
                "sha256": SHA,
                "size": 7,
                "name": "m",
                "kind": "manifest",
            },
            schema_version="urn:wdf:schema:job.accepted:1.0",
            traceparent=TRACE,
            seed=3,
            parameters={"a": 1},
            fingerprint="b" * 64,
        )
        insert_accepted(self.db, [row])
        self.move(job_id, status, terminal_at)
        return job_id

    def move(self, job_id: str, status: str, terminal_at: datetime | None = None) -> None:
        with self.db.transaction():
            self.db.execute("SET LOCAL session_replication_role = replica")
            self.db.execute(
                "UPDATE accepted_job SET status = %s, terminal_at = %s,"
                " published_at = CASE WHEN %s = 'accepted' THEN published_at"
                " ELSE coalesce(published_at, now()) END WHERE job_id = %s",
                (status, terminal_at, status, job_id),
            )

    def result(self, job: str, task: str = "sim", attempt: str = "r0a1", seed: int = 0):
        """A result object and its index row."""
        reference = self.worker.put_attempt(
            f"result of {job} {task} {attempt} {seed}".encode(),
            "result",
            "result.xml",
            ProducerRef(job=job, task=task, attempt=attempt),
            self.tenant,
            self.batch,
            seed,
        )
        index_reference(self.db, reference)
        return reference

    def zombie(self, job: str, attempt: str = "r0a1", task: str = "sim", seed: int = 0) -> str:
        """An attempt's object that no row names, as a late zombie upload leaves it."""
        key = f"{self.tenant}/{self.batch}/{job}/{seed}/{task}/{attempt}/result.xml"
        self.admin.put_object(Bucket="wdf-results", Key=key, Body=b"late")
        return key

    def summary(self, job: str):
        reference = self.aggregate.put_attempt(
            b"summary",
            "summary",
            "summary.json",
            ProducerRef(job=job, task="agg", attempt="r0a1"),
            self.tenant,
            self.batch,
        )
        index_reference(self.db, reference)
        return reference

    def checkpoint(self, job: str, segment: int, task: str = "sim", attempt: str = "r0a1"):
        """A checkpoint written through the client and indexed."""
        reference = write_checkpoint(
            self.worker,
            hashlib.shake_256(f"state {job} {segment}".encode()).digest(512),
            ProducerRef(job=job, task=task, attempt=attempt),
            self.tenant,
            self.batch,
            0,
            str(segment),
            {**META, "progress": segment},
        )
        index_reference(self.db, reference)
        return reference

    def bulk(self, job: str, count: int) -> None:
        """Many small objects under one job, put in parallel, with no rows."""
        prefix = f"{self.tenant}/{self.batch}/{job}"

        def put(n: int) -> None:
            self.admin.put_object(
                Bucket="wdf-results", Key=f"{prefix}/{n}/sim/r0a1/result.xml", Body=b"x"
            )

        with ThreadPoolExecutor(16) as pool:
            list(pool.map(put, range(count)))

    def keys(self, bucket: str, prefix: str | None = None) -> list[str]:
        prefix = self.tenant + "/" if prefix is None else prefix
        found: list[str] = []
        for page in self.admin.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix
        ):
            found += [item["Key"] for item in page.get("Contents", [])]
        return sorted(found)

    def live_rows(self, job: str) -> int:
        return self.db.execute(
            "SELECT count(*) FROM artifact WHERE producer_job = %s AND deleted_at IS NULL", (job,)
        ).fetchone()[0]

    def deleted_rows(self, job: str) -> int:
        return self.db.execute(
            "SELECT count(*) FROM artifact WHERE producer_job = %s AND deleted_at IS NOT NULL",
            (job,),
        ).fetchone()[0]


@pytest.fixture
def world(shelf, db, request):
    tenant = "t" + hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
    world = World(shelf, db, tenant)
    yield world
    world.clear_content()


class Clock:
    """The time that the callers of the client stamp their links with, moved by the test."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def advance(self, **delta: float) -> datetime:
        self.now += timedelta(**delta)
        return self.now


@pytest.fixture
def clock(monkeypatch):
    """A clock for the real callers: `put_content`, `commit` and `cache.use` link at its time."""
    clock = Clock()
    for module in (client_module, commit_module, cache_module):
        monkeypatch.setattr(module, "wall_now", lambda: clock.now)
    return clock


@pytest.fixture
def artifacts(login):
    """The login of the dispatch API and of the workflow's record activity."""
    return login("wdf_artifacts")


@pytest.fixture
def reap(reaper, store):
    """Runs the reaper at a time `later` than the fixed now, or with another login."""

    def run(later: timedelta = timedelta(0), conn=None, **kwargs):
        return reap_once(conn or reaper, store, NOW + later, WINDOWS, **kwargs)

    return run


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def windows():
    return WINDOWS


@pytest.fixture
def ago():
    """A moment `days` and `seconds` before the fixed now."""
    return lambda days=0, seconds=0: NOW - timedelta(days=days, seconds=seconds)
