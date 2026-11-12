"""A store and a database for the control activities, each one container for a test module.

Every activity runs as its writer: the store client holds the `workflow` identity and the database
login is a fresh member of `wdf_workflow`. The superuser only seeds and reads rows.
"""

import time
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_events import from_bytes
from wdf_jobstate import JobRow, insert_accepted, transition
from wdf_storecheck.fixtures import running
from wdf_storecheck.s3 import client as s3_client

NO_WAIT = RetryPolicy(sleep=lambda seconds: None)
STARTED = "contracts/events/vectors/golden/job.started/1.0.json"


@pytest.fixture(scope="module")
def shelf(t1, tmp_path_factory):
    with running(t1, "seaweedfs", tmp_path_factory.mktemp("shelf")) as started:
        started.store().create_buckets()
        yield started


@pytest.fixture
def db(appdb):
    with psycopg.connect(appdb, autocommit=True) as connection:
        yield connection


@pytest.fixture
def login(appdb, db):
    """The DSN of a fresh login user in a group role, dropped after the test."""
    users: list[str] = []

    def dsn(group: str) -> str:
        user = f"standin_{group}_{time.time_ns()}"
        db.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'standin' IN ROLE {}").format(
                sql.Identifier(user), sql.Identifier(group)
            )
        )
        users.append(user)
        return make_conninfo(appdb, user=user, password="standin")

    yield dsn
    for user in users:
        db.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(user)))
        db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(user)))


@pytest.fixture
def workflow_store(shelf):
    return ArtifactClient(shelf.endpoint, shelf.credentials["workflow"], NO_WAIT)


@pytest.fixture
def submit(shelf):
    """The dispatch API's client, which puts the manifests that load_plan reads."""
    return ArtifactClient(shelf.endpoint, shelf.credentials["submit"], NO_WAIT)


@pytest.fixture
def admin(shelf):
    return s3_client(shelf.endpoint, shelf.credentials["admin"])


@pytest.fixture
def started_event(root):
    return from_bytes((root / STARTED).read_bytes())


@pytest.fixture
def job(db, started_event):
    """A job row of the started event's tenant, moved from `accepted` along `path`."""

    def seed(*path: str) -> str:
        job_id = str(uuid.uuid4())
        row = JobRow(
            job_id=job_id,
            tenant=started_event.tenant,
            submission_key=f"key-{job_id}",
            batch_id=str(uuid.uuid4()),
            priority_class=started_event.priorityclass,
            manifest_ref={
                "uri": f"s3://wdf-sources/sha256/aa/{'a' * 64}",
                "sha256": "a" * 64,
                "size": 7,
                "name": "manifest.json",
                "kind": "manifest",
            },
            schema_version="urn:wdf:schema:job.accepted:1.0",
            traceparent=started_event.traceparent,
            seed=7,
            parameters={},
            fingerprint="b" * 64,
        )
        insert_accepted(db, [row])
        status = "accepted"
        for to in path:
            transition(db, job_id, status, to)
            status = to
        return job_id

    return seed


@pytest.fixture
def status(db):
    return lambda job_id: db.execute(
        "SELECT status FROM accepted_job WHERE job_id = %s", (job_id,)
    ).fetchone()[0]


@pytest.fixture
def stored(db):
    """The event ids and times stored for a job, in order."""
    return lambda job_id: db.execute(
        "SELECT event_id, event_time FROM event WHERE job_id = %s ORDER BY event_time, event_id",
        (job_id,),
    ).fetchall()
