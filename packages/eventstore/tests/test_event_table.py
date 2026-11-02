"""The event table and its policies against a real TimescaleDB, through the migration runner."""

from pathlib import Path

import psycopg
import pytest
import yaml
from wdf_eventstore import STUBBED
from wdf_eventstore.gen import render_policies
from wdf_eventstore.models import UUID_PATTERN, EventRow, Retention

pytestmark = pytest.mark.integration

RETENTION = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "contracts/state/retention.yaml").read_text()
)
JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
SQL_TYPES = {
    "timestamptz": "timestamp with time zone",
    "uuid": "uuid",
    "text": "text",
    "integer": "integer",
}


def row(db, event_id="/wdf/workflow#1", time="2026-10-30T12:00:00.123456Z", **extra):
    values = {
        "event_id": event_id,
        "event_time": time,
        "type": "job.started",
        "source": "/wdf/workflow",
        "tenant": "acme",
        "job_id": JOB,
        "body": "{}",
        **extra,
    }
    columns = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    return db.execute(f"INSERT INTO event ({columns}) VALUES ({marks})", list(values.values()))


@pytest.fixture
def db(appdb):
    with psycopg.connect(appdb, autocommit=True) as connection:
        yield connection


def model_columns() -> list[tuple[str, str, bool]]:
    """Name, SQL type and nullability of each model field, in the order of the model."""
    schema = EventRow.model_json_schema()
    columns = []
    for name, field in schema["properties"].items():
        options = field.get("anyOf", [field])
        nullable = any(o["type"] == "null" for o in options)
        shape = next(o for o in options if o["type"] != "null")
        if shape.get("format") == "date-time":
            sql = "timestamptz"
        elif shape.get("pattern") == UUID_PATTERN:
            sql = "uuid"
        else:
            sql = {"string": "text", "integer": "integer"}[shape["type"]]
        columns.append((name, SQL_TYPES[sql], nullable))
    return columns


def test_ddl_conformance(db):
    actual = db.execute(
        "SELECT column_name, data_type, is_nullable = 'YES', column_default"
        " FROM information_schema.columns WHERE table_name = 'event' ORDER BY ordinal_position"
    ).fetchall()
    if not actual:
        assert "event" in STUBBED
        return
    assert [(n, t, null) for n, t, null, _ in actual] == model_columns()
    assert [default for *_, default in actual] == [None] * len(actual)


def test_event_is_a_hypertable_on_event_time(db):
    dimension = db.execute(
        "SELECT column_name, time_interval FROM timescaledb_information.dimensions"
        " WHERE hypertable_name = 'event'"
    ).fetchall()
    assert [(name, str(interval)) for name, interval in dimension] == [
        ("event_time", "1 day, 0:00:00")
    ]
    assert db.execute(
        "SELECT compression_enabled FROM timescaledb_information.hypertables"
        " WHERE hypertable_name = 'event'"
    ).fetchone() == (True,)
    assert db.execute(
        "SELECT segmentby FROM timescaledb_information.hypertable_compression_settings"
        " WHERE hypertable = 'event'::regclass AND segmentby IS NOT NULL"
    ).fetchone() == ("job_id",)


def test_unique_key_is_event_id_and_event_time(db):
    keys = db.execute(
        "SELECT array_agg(a.attname ORDER BY a.attnum) FROM pg_index i"
        " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)"
        " WHERE i.indrelid = 'event'::regclass AND i.indisunique GROUP BY i.indexrelid"
    ).fetchall()
    assert [["event_id", "event_time"]] == sorted(k[0] for k in keys)
    row(db)
    with pytest.raises(psycopg.errors.UniqueViolation):
        row(db)
    row(db, time="2026-10-30T12:00:00.123457Z")


def test_policies_carry_the_intervals_of_retention_yaml(db):
    jobs = dict(
        db.execute(
            "SELECT proc_name, config FROM timescaledb_information.jobs"
            " WHERE hypertable_name = 'event' AND proc_name LIKE 'policy_%'"
        ).fetchall()
    )
    assert jobs.keys() == {"policy_compression", "policy_retention"}
    compress = RETENTION["housekeeping"]["event"]["compress_after_days"]
    drop = RETENTION["windows"]["event"]["drop_after_days"]
    assert jobs["policy_compression"]["compress_after"] == f"{compress} days"
    assert jobs["policy_retention"]["drop_after"] == f"{drop} days"


def test_attempt_columns_have_no_default(db):
    defaults = db.execute(
        "SELECT column_name, column_default FROM information_schema.columns"
        " WHERE table_name = 'event' AND column_name IN ('attempt', 'task_id')"
    ).fetchall()
    assert sorted(defaults) == [("attempt", None), ("task_id", None)]
    row(db)
    assert db.execute("SELECT task_id, attempt FROM event").fetchone() == (None, None)


def test_body_column_is_text_and_keeps_key_order(db):
    unsorted_body = '{"b":1,"a":{"d":1,"c":2}}'
    assert db.execute(
        "SELECT data_type FROM information_schema.columns"
        " WHERE table_name = 'event' AND column_name = 'body'"
    ).fetchone() == ("text",)
    row(db, body=unsorted_body)
    assert db.execute("SELECT body FROM event").fetchone() == (unsorted_body,)


def test_a_moved_window_replaces_the_policy_and_leaves_one_job_each(db):
    moved = Retention.model_validate(RETENTION)
    moved.windows["event"].drop_after_days = 21
    moved.housekeeping["event"].compress_after_days = 3
    db.execute(render_policies(moved))
    jobs = db.execute(
        "SELECT proc_name, config FROM timescaledb_information.jobs"
        " WHERE hypertable_name = 'event' AND proc_name LIKE 'policy_%' ORDER BY proc_name"
    ).fetchall()
    assert [
        (name, config.get("compress_after") or config["drop_after"]) for name, config in jobs
    ] == [
        ("policy_compression", "3 days"),
        ("policy_retention", "21 days"),
    ]
