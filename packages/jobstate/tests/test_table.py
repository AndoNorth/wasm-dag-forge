"""The accepted_job table of the migrations against a real Postgres."""

import json
import re
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml
from psycopg import errors
from wdf_jobstate import STUBBED
from wdf_jobstate.models import CLASSES, STATUSES, JobRow

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "contracts/state/README.md").read_text()
CONTRACT = yaml.safe_load((ROOT / "contracts/status/status.yaml").read_text())
SHA = "a" * 64
MANIFEST = {
    "uri": f"s3://wdf-sources/sha256/aa/{SHA}",
    "sha256": SHA,
    "size": 7,
    "name": "m",
    "kind": "manifest",
}

ROW = {
    "job_id": None,
    "tenant": "acme",
    "submission_key": "k1",
    "batch_id": "6a1c1a1e-2f64-4f0e-9a58-0d3f1b9a3c11",
    "priority_class": "standard",
    "manifest_ref": json.dumps(MANIFEST),
    "schema_version": "wdf.job.accepted/3",
    "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    "seed": 1,
    "parameters": "{}",
    "fingerprint": "b" * 64,
}


def insert(db, **overrides):
    row = {**ROW, "job_id": str(uuid.uuid4()), **overrides}
    columns = ", ".join(row)
    marks = ", ".join(f"%({name})s" for name in row)
    db.execute(f"INSERT INTO accepted_job ({columns}) VALUES ({marks})", row)
    return row["job_id"]


@pytest.fixture
def db(appdb):
    with psycopg.connect(appdb, autocommit=True) as connection:
        yield connection


def refused(db, error, sql, params=None):
    with pytest.raises(error):
        db.execute(sql, params)


def readme_columns():
    section = re.search(r"^## Table `accepted_job`$(.*?)(?=^## )", README, re.S | re.M).group(1)
    return re.findall(r"^\| `([a-z_0-9]+)` \| `([a-z0-9]+)` \|", section, re.M)


def test_the_table_is_no_longer_stubbed():
    assert "accepted_job" not in STUBBED


def test_ddl_conformance(db):
    """Columns, their order, types, nullability and defaults equal the pinned model and README."""
    found = db.execute(
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns"
        " WHERE table_name = 'accepted_job' ORDER BY ordinal_position"
    ).fetchall()
    types = {"timestamptz": "timestamp with time zone", "integer": "integer", "bigint": "bigint"}
    assert (
        [name for name, *_ in found]
        == list(JobRow.model_fields)
        == [n for n, _ in readme_columns()]
    )
    for (name, data_type, nullable, default), (_, declared) in zip(
        found, readme_columns(), strict=True
    ):
        assert data_type == types.get(declared, declared), name
        field = JobRow.model_fields[name]
        if name in ("created_at", "updated_at", "manifest_sha256"):
            continue
        default_value = None if field.is_required() else field.default
        assert (nullable == "YES") == (not field.is_required() and default_value is None), name
        expected = {None: None, "short": "'short'::text", "accepted": "'accepted'::text", 0: "0"}[
            default_value
        ]
        assert default == expected, name


def test_no_column_has_an_undeclared_default(db):
    defaults = dict(
        db.execute(
            "SELECT column_name, column_default FROM information_schema.columns"
            " WHERE table_name = 'accepted_job' AND column_default IS NOT NULL"
        ).fetchall()
    )
    assert set(defaults) == {"status", "job_class", "control_seq", "created_at", "updated_at"}


def test_status_set_is_the_ten_of_the_contract(db):
    held = {r[0] for r in db.execute("SELECT status FROM job_status")}
    assert held == set(STATUSES) == set(CONTRACT["statuses"]) and len(held) == 10
    job = insert(db)
    db.execute("UPDATE accepted_job SET status = 'admitted' WHERE job_id = %s", (job,))
    db.execute("UPDATE accepted_job SET status = 'running' WHERE job_id = %s", (job,))
    db.execute("UPDATE accepted_job SET status = 'partial' WHERE job_id = %s", (job,))
    assert db.execute("SELECT status FROM accepted_job WHERE job_id = %s", (job,)).fetchone() == (
        "partial",
    )
    for name in ("queued", "pausing"):
        with pytest.raises(errors.CheckViolation):
            insert(db, submission_key=name, status=name)


def test_a_row_may_be_inserted_in_any_contract_status(db):
    for status in STATUSES:
        insert(db, submission_key=f"s-{status}", status=status)


def test_job_class_default_is_short_and_checked_against_limits(db):
    job = insert(db)
    assert db.execute(
        "SELECT job_class FROM accepted_job WHERE job_id = %s", (job,)
    ).fetchone() == ("short",)
    for job_class in CLASSES:
        insert(db, submission_key=f"c-{job_class}", job_class=job_class)
    refused(
        db,
        errors.CheckViolation,
        "UPDATE accepted_job SET job_class = 'simulate' WHERE job_id = %s",
        (job,),
    )


def test_unique_tenant_submission_key_is_the_one_dedup_point(db):
    insert(db)
    with pytest.raises(errors.UniqueViolation):
        insert(db)
    insert(db, tenant="other")
    insert(db, submission_key="k2")


def test_parameters_must_be_an_object(db):
    for bad in ("[]", "1", '"x"', "null"):
        with pytest.raises(errors.CheckViolation):
            insert(db, parameters=bad)


def test_seed_is_below_two_to_the_53(db):
    insert(db, submission_key="top", seed=2**53 - 1)
    insert(db, submission_key="zero", seed=0)
    for bad in (2**53, -1):
        with pytest.raises(errors.CheckViolation):
            insert(db, seed=bad)


def test_manifest_hash_is_generated_and_indexed(db):
    job = insert(db)
    assert db.execute(
        "SELECT manifest_sha256 FROM accepted_job WHERE job_id = %s", (job,)
    ).fetchone() == (SHA,)
    with pytest.raises(errors.GeneratedAlways):
        insert(db, submission_key="g", manifest_sha256="c" * 64)
    indexes = {
        r[0]
        for r in db.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'accepted_job'")
    }
    assert {
        "accepted_job_batch_status",
        "accepted_job_unpublished",
        "accepted_job_manifest_sha256",
    } <= indexes


def test_row_values_are_checked(db):
    for column, bad in (
        ("fingerprint", "ABC"),
        ("submission_key", "k" * 257),
        ("submission_key", ""),
        ("requested_action", "pausing"),
        ("control_seq", -1),
        ("manifest_ref", json.dumps({**MANIFEST, "kind": "source"})),
        ("pending_patch_ref", "[]"),
    ):
        with pytest.raises(errors.CheckViolation):
            insert(db, **{"submission_key": "v", column: bad})


def test_updated_at_moves_on_every_change(db):
    job = insert(db)
    before = db.execute("SELECT updated_at FROM accepted_job WHERE job_id = %s", (job,)).fetchone()[
        0
    ]
    db.execute("UPDATE accepted_job SET control_seq = 1 WHERE job_id = %s", (job,))
    after = db.execute("SELECT updated_at FROM accepted_job WHERE job_id = %s", (job,)).fetchone()[
        0
    ]
    assert after > before


def test_transition_table_equals_the_contract(db):
    held = set(db.execute("SELECT from_status, to_status, row_only FROM job_transition").fetchall())
    expected = {(t["from"], t["to"], t.get("row_only", False)) for t in CONTRACT["transitions"]}
    assert held == expected and len(held) == 22
    terminal = {r[0] for r in db.execute("SELECT status FROM job_status WHERE terminal")}
    assert terminal == set(CONTRACT["terminal"])


def test_trigger_refuses_a_move_outside_the_table(db):
    job = insert(db)
    with pytest.raises(errors.CheckViolation, match="not in job_transition"):
        db.execute("UPDATE accepted_job SET status = 'running' WHERE job_id = %s", (job,))
    db.execute("UPDATE accepted_job SET status = 'admitted' WHERE job_id = %s", (job,))
    with pytest.raises(errors.CheckViolation):
        db.execute("UPDATE accepted_job SET status = 'accepted' WHERE job_id = %s", (job,))
    db.execute(
        "UPDATE accepted_job SET status = 'admitted', control_seq = 2 WHERE job_id = %s", (job,)
    )


def test_the_vocabulary_file_can_be_applied_again_over_live_rows(db):
    from wdf_jobstate.gen import VOCAB

    job = insert(db)
    db.execute("UPDATE accepted_job SET status = 'admitted' WHERE job_id = %s", (job,))
    db.execute((ROOT / VOCAB).read_text())
    assert db.execute("SELECT count(*) FROM job_status").fetchone() == (10,)
    assert db.execute("SELECT status FROM accepted_job WHERE job_id = %s", (job,)).fetchone() == (
        "admitted",
    )
