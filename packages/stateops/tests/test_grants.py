"""The group roles, each used through a stand-in login user and the library its writer calls."""

import itertools
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.conninfo import make_conninfo
from wdf_artifactindex import (
    Deleted,
    Indexed,
    Invalidated,
    Linked,
    Purged,
    Revived,
    Won,
    expired_cache_entries,
    index_reference,
    invalidate_cache_entry,
    link_use,
    mark_deleted,
    purge,
    record_cache_entry,
    tombstone,
    touch_cache_entry,
    uses_of,
)
from wdf_events import build_event
from wdf_eventstore import insert_events
from wdf_jobstate import (
    JobRow,
    Moved,
    insert_accepted,
    mark_delivered,
    mark_published,
    request_action,
    transition,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "contracts/state/README.md").read_text()
ROLES_SQL = (resources.files("wdf_stateops.migrations") / "R__roles.sql").read_text()
TABLES = (
    "accepted_job",
    "job_status",
    "job_transition",
    "event",
    "artifact",
    "artifact_use",
    "reaper_job",
    "reaper_cursor",
)
# Rows take created_at from the database clock, so the times passed in follow the real clock.
NOW = datetime.now(UTC)
ARTIFACT_WRITES = {"last_used_at", "deleted_at", "purged_at", "cache_key"}
REAPER_READS = "job_id, tenant, batch_id, status, terminal_at, manifest_ref, pending_patch_ref"
LATER = NOW + timedelta(days=10)
GRACE = timedelta(days=2)
INTERVAL = timedelta(hours=1)
SHA = "a" * 64
KEYS = itertools.count()


def job_row() -> JobRow:
    return JobRow(
        job_id=str(uuid.uuid4()),
        tenant="acme",
        submission_key=f"key-{next(KEYS)}",
        batch_id=str(uuid.uuid4()),
        priority_class="standard",
        manifest_ref={
            "uri": f"s3://wdf-sources/sha256/aa/{SHA}",
            "sha256": SHA,
            "size": 7,
            "name": "m",
            "kind": "manifest",
        },
        schema_version="wdf.job.accepted/3",
        traceparent="00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        seed=3,
        parameters={"a": 1},
        fingerprint="b" * 64,
    )


def some_event():
    return build_event(
        "job.started",
        source="/wdf/workflow",
        tenant="acme",
        jobid=str(uuid.uuid4()),
        priorityclass="standard",
        jobclass="short",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        time=NOW,
        data={},
        sequence=next(KEYS),
    )


def reference(n: int) -> dict:
    return {
        "uri": f"s3://wdf-components/sha256/00/{n:064x}",
        "sha256": f"{n:064x}",
        "size": 1,
        "name": "c.wasm",
        "kind": "component",
    }


@pytest.fixture
def login(appdb):
    """Opens a connection as a fresh login user that is a member of the given group role."""
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
def seeded(login):
    """One accepted job inserted through the dispatch role, as its id."""
    row = job_row()
    assert insert_accepted(login("wdf_dispatch"), [row]).inserted == [row.job_id]
    return row.job_id


def refused(conn, statement: str, params=()) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement, params)


def writes_granted(conn, role: str, table: str, privilege: str) -> bool:
    """Whether the role holds the privilege on the table or, where it applies, on a column."""
    query = "has_table_privilege(%(r)s, %(t)s, %(p)s)"
    if privilege not in ("DELETE", "TRUNCATE"):
        query += " OR has_any_column_privilege(%(r)s, %(t)s, %(p)s)"
    return conn.execute(f"SELECT {query}", {"r": role, "t": table, "p": privilege}).fetchone()[0]


def test_the_roles_cannot_log_in_and_are_those_of_the_writers_table(appdb):
    stated = set(re.findall(r"role `(wdf_\w+)`", README))
    with psycopg.connect(appdb) as conn:
        found = dict(
            conn.execute("SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'wdf\\_%'")
        )
    assert stated == {"wdf_dispatch", "wdf_control", "wdf_admission", "wdf_workflow"} | {
        "wdf_artifacts",
        "wdf_reaper",
    }
    assert set(found) == stated | {"wdf_reader"}
    assert not any(found.values())


def test_every_table_of_the_three_families_has_a_writer(appdb):
    """A column level grant is not in the table level view, so it is read from its own."""
    with psycopg.connect(appdb) as conn:
        writers = conn.execute(
            "SELECT table_name, grantee FROM information_schema.role_table_grants"
            " WHERE privilege_type = 'INSERT' AND grantee LIKE 'wdf\\_%'"
            " UNION SELECT table_name, grantee FROM information_schema.column_privileges"
            " WHERE privilege_type = 'INSERT' AND grantee LIKE 'wdf\\_%'"
        ).fetchall()
    for table in (
        "accepted_job",
        "event",
        "artifact",
        "artifact_use",
        "reaper_job",
        "reaper_cursor",
    ):
        assert [grantee for name, grantee in writers if name == table], table


def test_workflow_role_cannot_insert_an_accepted_job(login):
    workflow = login("wdf_workflow")
    with pytest.raises(errors.InsufficientPrivilege):
        insert_accepted(workflow, [job_row()])


def test_dispatch_role_cannot_update_status(login, seeded):
    dispatch = login("wdf_dispatch")
    mark_published(dispatch, seeded)
    with pytest.raises(errors.InsufficientPrivilege):
        transition(dispatch, seeded, "accepted", "admitted")
    refused(dispatch, "UPDATE accepted_job SET requested_action = 'cancel'")
    refused(
        dispatch, "INSERT INTO accepted_job (job_id, status) VALUES (gen_random_uuid(), 'running')"
    )


def test_control_role_writes_only_the_control_columns_and_row_only_moves(login, seeded):
    control = login("wdf_control")
    asked = request_action(control, seeded, "pause")
    mark_delivered(control, seeded, asked.control_seq)
    control.execute(
        "UPDATE accepted_job SET pending_patch_ref = '{}'::jsonb WHERE job_id = %s", (seeded,)
    )
    assert transition(control, seeded, "accepted", "paused", row_only=True) == Moved("paused")
    for column in ("tenant", "manifest_ref", "published_at", "seed", "fingerprint", "created_at"):
        refused(control, f"UPDATE accepted_job SET {column} = {column}")
    with pytest.raises(errors.InsufficientPrivilege):
        insert_accepted(control, [job_row()])
    refused(control, "DELETE FROM accepted_job")


def test_admission_role_inserts_events_and_moves_to_admitted(login, seeded):
    admission = login("wdf_admission")
    assert insert_events(admission, [some_event()]).inserted == 1
    assert transition(admission, seeded, "accepted", "admitted") == Moved("admitted")
    refused(admission, "UPDATE accepted_job SET control_seq = control_seq")
    refused(admission, "UPDATE event SET type = type")


def test_workflow_role_moves_the_row_and_inserts_events(login, seeded):
    workflow = login("wdf_workflow")
    transition(login("wdf_admission"), seeded, "accepted", "admitted")
    assert transition(workflow, seeded, "admitted", "running") == Moved("running")
    assert insert_events(workflow, [some_event()]).inserted == 1
    refused(workflow, "UPDATE accepted_job SET published_at = now()")


def columns(conn, table: str) -> list[str]:
    return [
        name
        for (name,) in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s"
            " ORDER BY ordinal_position",
            (table,),
        )
    ]


def test_artifacts_role_indexes_references_and_cache_entries(login):
    artifacts = login("wdf_artifacts")
    assert isinstance(index_reference(artifacts, reference(1)), Indexed)
    entry = {
        "cache_key": "c" * 64,
        "wasm_uri": reference(1)["uri"],
        "componentize_py_version": "0.17.0",
        "wit_version": "1.0.0",
    }
    assert isinstance(record_cache_entry(artifacts, entry), Won)
    touch_cache_entry(artifacts, "c" * 64, NOW)
    refused(artifacts, "DELETE FROM artifact")


def test_artifacts_role_cannot_soft_delete_outside_its_columns(login, appdb):
    artifacts = login("wdf_artifacts")
    index_reference(artifacts, reference(4))
    with psycopg.connect(appdb) as admin:
        names = columns(admin, "artifact")
    for column in set(names) - ARTIFACT_WRITES:
        refused(artifacts, f"UPDATE artifact SET {column} = DEFAULT")
    for column in ("row_id", "created_at", "deleted_at", "purged_at"):
        refused(artifacts, f"INSERT INTO artifact ({column}, kind) VALUES (DEFAULT, 'manifest')")
    refused(artifacts, "DELETE FROM artifact_use")
    refused(artifacts, "UPDATE artifact_use SET job_id = job_id")
    refused(artifacts, "UPDATE artifact_use SET created_at = created_at")
    refused(artifacts, "SELECT job_id FROM accepted_job")
    for table in ("reaper_job", "reaper_cursor"):
        refused(artifacts, f"SELECT count(*) FROM {table}")


def test_reaper_reads_what_it_needs_and_cannot_update_a_job_row(login, seeded, appdb):
    reaper = login("wdf_reaper")
    index_reference(login("wdf_artifacts"), reference(2))
    assert reaper.execute(f"SELECT {REAPER_READS} FROM accepted_job").fetchall()
    for table in ("job_status", "artifact", "artifact_use", "reaper_job", "reaper_cursor"):
        reaper.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
    with psycopg.connect(appdb) as admin:
        job_columns = columns(admin, "accepted_job")
        artifact_columns = columns(admin, "artifact")
    for column in set(job_columns) - set(REAPER_READS.split(", ")):
        refused(reaper, f"SELECT {column} FROM accepted_job")
    for column in job_columns:
        refused(reaper, f"UPDATE accepted_job SET {column} = DEFAULT")
    for column in set(artifact_columns) - ARTIFACT_WRITES:
        refused(reaper, f"UPDATE artifact SET {column} = DEFAULT")
    refused(reaper, "UPDATE artifact_use SET last_linked_at = now()")
    refused(reaper, "DELETE FROM artifact_use")


def test_reaper_role_updates_only_its_columns(login):
    index_reference(login("wdf_artifacts"), reference(2))
    reaper = login("wdf_reaper")
    assert isinstance(mark_deleted(reaper, reference(2)["uri"], NOW), Deleted)
    refused(reaper, "UPDATE artifact SET last_used_at = now()")
    refused(reaper, "UPDATE artifact SET uri = uri")
    refused(reaper, "DELETE FROM artifact")
    with pytest.raises(errors.InsufficientPrivilege):
        index_reference(reaper, reference(3))
    refused(reaper, "UPDATE accepted_job SET status = status")


def test_only_the_reaper_role_writes_the_progress_tables(login, seeded, appdb):
    reaper = login("wdf_reaper")
    reaper.execute("INSERT INTO reaper_job (job_id) VALUES (%s)", (seeded,))
    reaper.execute("UPDATE reaper_job SET results_done_at = %s WHERE job_id = %s", (NOW, seeded))
    reaper.execute("INSERT INTO reaper_cursor (name, position) VALUES ('orphans', 'sources/ab')")
    reaper.execute("UPDATE reaper_cursor SET position = 'sources/cd', updated_at = now()")
    refused(reaper, "DELETE FROM reaper_job")
    refused(reaper, "DELETE FROM reaper_cursor")
    with psycopg.connect(appdb) as admin:
        roles = [
            r
            for (r,) in admin.execute(
                "SELECT rolname FROM pg_roles"
                " WHERE rolname LIKE 'wdf\\_%' AND rolname <> 'wdf_reaper'"
            )
        ]
        for role, table in itertools.product(roles, ("reaper_job", "reaper_cursor")):
            for privilege in ("INSERT", "UPDATE"):
                assert not writes_granted(admin, role, table, privilege), (role, table, privilege)


def test_record_cache_entry_works_under_the_real_wdf_artifacts_login(login):
    artifacts = login("wdf_artifacts")
    wasm, info = reference(10), {**reference(11), "kind": "build-info", "name": "build-info.json"}
    for ref in (wasm, info):
        assert isinstance(index_reference(artifacts, ref), Indexed)
    entry = {
        "cache_key": "d" * 64,
        "wasm_uri": wasm["uri"],
        "build_info_uri": info["uri"],
        "componentize_py_version": "0.17.0",
        "wit_version": "1.0.0",
    }
    assert isinstance(record_cache_entry(artifacts, entry), Won)
    assert isinstance(invalidate_cache_entry(artifacts, "d" * 64, NOW), Invalidated)
    assert isinstance(record_cache_entry(artifacts, entry), Won)


def test_reaper_login_can_expire_an_entry_and_retire_its_key(login):
    artifacts, reaper = login("wdf_artifacts"), login("wdf_reaper")
    index_reference(artifacts, reference(12))
    entry = {
        "cache_key": "e" * 64,
        "wasm_uri": reference(12)["uri"],
        "componentize_py_version": "0.17.0",
        "wit_version": "1.0.0",
        "last_used_at": NOW,
    }
    assert isinstance(record_cache_entry(artifacts, entry), Won)
    assert expired_cache_entries(reaper, NOW + timedelta(days=1), LATER) == ["e" * 64]
    assert isinstance(record_cache_entry(artifacts, entry), Won)


def test_now_follows_the_database_clock(appdb):
    """A calendar date here would pass until the database clock overtook it."""
    with psycopg.connect(appdb) as conn:
        (database_now,) = conn.execute("SELECT now()").fetchone()
    assert abs(database_now - NOW) < timedelta(hours=1)


def test_put_content_flow_works_under_the_wdf_artifacts_login(login):
    """Index, revive and link, as the connection of the dispatch API does."""
    artifacts, reaper = login("wdf_artifacts"), login("wdf_reaper")
    job, uri = str(uuid.uuid4()), reference(13)["uri"]
    assert isinstance(index_reference(artifacts, reference(13)), Indexed)
    assert isinstance(link_use(artifacts, job, uri, NOW), Linked)
    assert [use.job_id for use in uses_of(artifacts, uri)] == [job]

    other = reference(14)
    index_reference(artifacts, other)
    assert isinstance(tombstone(reaper, other["uri"], LATER, GRACE), Deleted)
    assert isinstance(link_use(artifacts, job, other["uri"], LATER), Revived)

    third = reference(15)
    index_reference(artifacts, third)
    assert isinstance(tombstone(reaper, third["uri"], LATER, GRACE), Deleted)
    assert isinstance(purge(reaper, third["uri"], LATER + 2 * INTERVAL, INTERVAL, GRACE), Purged)
    assert isinstance(index_reference(artifacts, third), Revived)
    assert isinstance(link_use(artifacts, job, third["uri"], LATER), Linked)


def test_retried_link_renews_under_the_wdf_artifacts_login(login):
    artifacts = login("wdf_artifacts")
    job, uri = str(uuid.uuid4()), reference(16)["uri"]
    index_reference(artifacts, reference(16))
    assert isinstance(link_use(artifacts, job, uri, NOW), Linked)
    assert isinstance(link_use(artifacts, job, uri, LATER), Linked)
    (use,) = uses_of(artifacts, uri)
    assert (use.created_at, use.last_linked_at) == (NOW, LATER)


def test_reader_role_cannot_write_any_table(login, seeded):
    reader = login("wdf_reader")
    for table in TABLES:
        reader.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert not writes_granted(reader, "wdf_reader", table, privilege), (table, privilege)
    refused(reader, "UPDATE accepted_job SET status = status")
    refused(reader, "DELETE FROM event")
    with pytest.raises(errors.InsufficientPrivilege):
        insert_events(reader, [some_event()])


def test_no_role_can_delete_truncate_or_rewrite_what_is_append_only(appdb):
    with psycopg.connect(appdb) as conn:
        roles = [
            r for (r,) in conn.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'wdf\\_%'")
        ]
        for role, table in itertools.product(roles, TABLES):
            for privilege in ("DELETE", "TRUNCATE"):
                assert not writes_granted(conn, role, table, privilege), (role, table, privilege)
        for role in roles:
            assert not writes_granted(conn, role, "event", "UPDATE"), role
            assert not writes_granted(conn, role, "job_status", "INSERT"), role


def test_status_value_is_not_restricted_by_grants(login, seeded):
    """A grant names columns, not values, so a writer may write any status the trigger allows."""
    control = login("wdf_control")
    control.execute("UPDATE accepted_job SET status = 'admitted' WHERE job_id = %s", (seeded,))
    with pytest.raises(errors.CheckViolation):
        control.execute("UPDATE accepted_job SET status = 'succeeded' WHERE job_id = %s", (seeded,))
    workflow = login("wdf_workflow")
    workflow.execute("UPDATE accepted_job SET status = 'running' WHERE job_id = %s", (seeded,))
    status = workflow.execute("SELECT status FROM accepted_job WHERE job_id = %s", (seeded,))
    assert status.fetchone() == ("running",)


def test_reapplying_the_file_restores_the_grants(appdb, login, seeded):
    with psycopg.connect(appdb, autocommit=True) as admin:
        admin.execute("GRANT UPDATE ON accepted_job TO wdf_reader")
        admin.execute("REVOKE UPDATE (published_at) ON accepted_job FROM wdf_dispatch")
        admin.execute(ROLES_SQL)
        admin.execute(ROLES_SQL)
        assert not writes_granted(admin, "wdf_reader", "accepted_job", "UPDATE")
    mark_published(login("wdf_dispatch"), seeded)


def test_roles_apply_again_in_a_second_database_of_the_same_container(appdb):
    second = "second_" + str(time.time_ns())
    with psycopg.connect(make_conninfo(appdb, dbname="postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(second)))
    dsn = make_conninfo(appdb, dbname=second)
    result = subprocess.run(
        [sys.executable, "-m", "wdf_migrate", "apply", "--dsn", dsn], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "applied R__roles.sql" in result.stdout
    with psycopg.connect(dsn) as conn:
        assert writes_granted(conn, "wdf_dispatch", "accepted_job", "INSERT")
