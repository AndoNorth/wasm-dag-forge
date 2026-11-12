"""A store and a database for the control activities, each one container for a test module.

Every activity runs as its writer: the store client holds the `workflow` identity and the database
login is a fresh member of `wdf_workflow` or `wdf_artifacts`. The superuser only seeds and reads
rows, and the clients of the other store roles write what a worker or the dispatch API would.
"""

import time
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from wdf_artifactclient.client import ArtifactClient, RetryPolicy
from wdf_artifactclient.keys import split_uri
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
def role(shelf):
    """A client of a store role of the contract."""
    return lambda name: ArtifactClient(shelf.endpoint, shelf.credentials[name], NO_WAIT)


@pytest.fixture
def dead_store(shelf):
    """A `workflow` client of a store that refuses every connection."""
    return ArtifactClient("http://127.0.0.1:1", shelf.credentials["workflow"], NO_WAIT)


@pytest.fixture
def batch(request):
    """A batch id that no other test of the module shares, so attempt keys never collide."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, request.node.nodeid))


@pytest.fixture
def rows(db):
    """The live index rows of the uris, as (uri, producer attempt) in uri order."""
    return lambda uris: db.execute(
        "SELECT uri, producer_attempt FROM artifact WHERE uri = ANY(%s) AND deleted_at IS NULL"
        " ORDER BY uri",
        (list(uris),),
    ).fetchall()


@pytest.fixture
def uses(db):
    """The uris that a job holds through artifact_use, in uri order."""
    return lambda job_id: [
        row[0]
        for row in db.execute(
            "SELECT a.uri FROM artifact_use u JOIN artifact a ON a.row_id = u.artifact_row_id"
            " WHERE u.job_id = %s ORDER BY a.uri",
            (job_id,),
        )
    ]


@pytest.fixture
def submit(shelf):
    """The dispatch API's client, which puts the manifests that load_plan reads."""
    return ArtifactClient(shelf.endpoint, shelf.credentials["submit"], NO_WAIT)


@pytest.fixture
def admin(shelf):
    return s3_client(shelf.endpoint, shelf.credentials["admin"])


@pytest.fixture
def vanish(admin):
    """Deletes the object of a reference, as a store that lost it would."""

    def delete(reference):
        bucket, key = split_uri(reference.uri)
        admin.delete_object(Bucket=bucket, Key=key)
        return reference

    return delete


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
