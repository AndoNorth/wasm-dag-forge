"""The table, its constraints and the generated repeatable file, against a real database."""

import re
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import get_args

import psycopg
import pytest
import yaml
from psycopg import errors
from wdf_artifactindex import Won, record_cache_entry
from wdf_artifactindex.gen import KINDS_SQL, LAYOUT
from wdf_artifactindex.models import LAYOUT as PACKAGE_LAYOUT
from wdf_artifactindex.models import ArtifactRow
from wdf_migrate import apply
from wdf_migrate.runner import from_dirs

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
README = (ROOT / "contracts/state/README.md").read_text()
MIGRATIONS = resources.files("wdf_artifactindex.migrations")
SHA = "a" * 64
JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
PRODUCER = ("producer_job", "producer_task", "producer_attempt")
KINDS = PACKAGE_LAYOUT["kinds"]
CONTENT_ADDRESSED = [k for k, v in KINDS.items() if v["class"] == "content-addressed"]
ATTEMPT_SCOPED = [k for k, v in KINDS.items() if v["class"] == "attempt-scoped"]


def reference_row(kind: str, n: int = 0, **override) -> dict:
    row = {
        "kind": kind,
        "uri": f"s3://wdf-test/{kind}/{n}",
        "bucket": "wdf-test",
        "key": f"{kind}/{n}",
        "sha256": SHA,
        "size": 1,
        "name": "x",
    }
    return row | override


def attempt_producer(attempt: str = "r0a1") -> dict:
    return {"producer_job": JOB, "producer_task": "simulate-0", "producer_attempt": attempt}


def insert(conn, row: dict) -> None:
    columns = ", ".join(row)
    conn.execute(
        f"INSERT INTO artifact ({columns}) VALUES ({', '.join(['%s'] * len(row))})",
        list(row.values()),
    )


def refused(conn, row: dict, error=errors.CheckViolation) -> None:
    with pytest.raises(error):
        with conn.transaction():
            insert(conn, row)


def cache_row(wasm_uri: str, key: str = SHA, **override) -> dict:
    row = {
        "kind": "compile-cache",
        "cache_key": key,
        "wasm_uri": wasm_uri,
        "componentize_py_version": "1.0",
        "wit_version": "2.0",
        "last_used_at": "2026-11-02T00:00:00Z",
    }
    return row | override


def kind_literals(sql: str) -> set[str]:
    """The kinds of the layout that a SQL text names as quoted literals, compile-cache excluded."""
    return {k for k in KINDS if f"'{k}'" in sql}


def test_0300_holds_no_kind_list():
    assert kind_literals((MIGRATIONS / "0300_artifact_index.sql").read_text()) == set()


def test_the_kind_list_check_sees_a_kind_and_exempts_the_cache_kind():
    assert kind_literals("CHECK (kind <> 'result')") == {"result"}
    assert kind_literals("CHECK (kind <> 'compile-cache')") == set()


def test_ddl_conformance(conn):
    """Columns, types, nullability and defaults equal the pinned model and the README table."""
    section = re.search(r"^## Table `artifact`$(.*?)(?=^## )", README, re.S | re.M).group(1)
    pinned = dict(re.findall(r"^\| `([a-z_0-9]+)` \| `([a-z]+)` \|", section, re.M))
    types = {"timestamptz": "timestamp with time zone"}
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable, column_default, is_identity,"
        " identity_generation FROM information_schema.columns WHERE table_name = 'artifact'"
    ).fetchall()
    assert {r[0] for r in rows} == set(ArtifactRow.model_fields) == set(pinned)
    database_set = {"row_id", "created_at"}
    for name, data_type, nullable, default, identity, generation in rows:
        field = ArtifactRow.model_fields[name]
        assert data_type == types.get(pinned[name], pinned[name]), name
        assert (nullable == "YES") == (
            name not in database_set and type(None) in get_args(field.annotation)
        ), name
        assert default == ("now()" if name == "created_at" else None), name
        assert (identity, generation) == (
            ("YES", "ALWAYS") if name == "row_id" else ("NO", None)
        ), name


def test_attempt_columns_have_no_default(conn):
    defaults = conn.execute(
        "SELECT column_default FROM information_schema.columns"
        " WHERE table_name = 'artifact' AND column_name = ANY(%s)",
        (list(PRODUCER),),
    ).fetchall()
    assert defaults == [(None,)] * 3
    refused(conn, reference_row("result", producer_job=JOB, producer_task="t"))


def test_a_row_is_either_a_reference_or_a_cache_entry(conn):
    insert(conn, reference_row("component", uri="s3://wdf-test/wasm", key="wasm"))
    insert(conn, cache_row("s3://wdf-test/wasm"))
    wasm = "s3://wdf-test/wasm"
    for bad in (
        reference_row("source", cache_key=SHA),
        reference_row("source", wasm_uri=wasm),
        reference_row("source", last_used_at="2026-11-02T00:00:00Z"),
        {k: v for k, v in reference_row("source").items() if k != "uri"},
        {k: v for k, v in reference_row("source").items() if k != "sha256"},
        cache_row(wasm, key="b" * 64, uri="s3://wdf-test/cache"),
        cache_row(wasm, key="b" * 64, sha256=SHA),
        {k: v for k, v in cache_row(wasm, key="b" * 64).items() if k != "wasm_uri"},
        {k: v for k, v in cache_row(wasm, key="b" * 64).items() if k != "last_used_at"},
    ):
        refused(conn, bad)


def test_a_cache_row_has_null_producer_columns(conn):
    insert(conn, reference_row("component", uri="s3://wdf-test/wasm", key="wasm"))
    for column, value in attempt_producer().items():
        refused(conn, cache_row("s3://wdf-test/wasm", key="c" * 64, **{column: value}))
    entry = {k: v for k, v in cache_row("s3://wdf-test/wasm", key="d" * 64).items() if k != "kind"}
    assert isinstance(record_cache_entry(conn, entry), Won)
    assert conn.execute(
        "SELECT producer_job, producer_task, producer_attempt FROM artifact"
        " WHERE kind = 'compile-cache'"
    ).fetchall() == [(None, None, None)]


def test_the_first_writer_of_a_cache_key_wins_at_the_index(conn):
    insert(conn, reference_row("component", uri="s3://wdf-test/wasm", key="wasm"))
    insert(conn, cache_row("s3://wdf-test/wasm"))
    refused(conn, cache_row("s3://wdf-test/wasm"), errors.UniqueViolation)


def test_raw_delete_of_a_pinned_wasm_is_refused_by_the_foreign_key(conn):
    insert(conn, reference_row("component", uri="s3://wdf-test/wasm", key="wasm"))
    insert(conn, cache_row("s3://wdf-test/wasm"))
    with pytest.raises(errors.ForeignKeyViolation):
        with conn.transaction():
            conn.execute("DELETE FROM artifact WHERE uri = 's3://wdf-test/wasm'")


def test_content_addressed_kinds_have_no_producer_and_attempt_scoped_kinds_have_one(conn):
    assert CONTENT_ADDRESSED and ATTEMPT_SCOPED
    assert set(CONTENT_ADDRESSED) | set(ATTEMPT_SCOPED) == set(KINDS)
    for n, kind in enumerate(CONTENT_ADDRESSED):
        insert(conn, reference_row(kind, n))
        for column, value in attempt_producer().items():
            refused(conn, reference_row(kind, n + 100, **{column: value}))
    for n, kind in enumerate(ATTEMPT_SCOPED):
        insert(conn, reference_row(kind, n, **attempt_producer()))
        for column in PRODUCER:
            refused(conn, reference_row(kind, n + 100, **attempt_producer() | {column: None}))
        refused(conn, reference_row(kind, n + 200, **attempt_producer("a1")))
    refused(conn, reference_row("not-a-kind"))
    refused(conn, reference_row("not-a-kind", **attempt_producer()))


def scratch_root(tmp_path: Path) -> Path:
    layout = yaml.safe_load((ROOT / LAYOUT).read_text())
    layout["kinds"]["scratch-blob"] = {
        "bucket": "wdf-sources",
        "class": "content-addressed",
        "key": "sha256/<hash2>/<hash>",
    }
    layout["kinds"]["scratch-run"] = {
        "bucket": "wdf-results",
        "class": "attempt-scoped",
        "key": "<tenant>/<batch>/<job>/<seed>/<task>/<attempt>/<name>",
    }
    (tmp_path / LAYOUT).parent.mkdir(parents=True)
    (tmp_path / LAYOUT).write_text(yaml.safe_dump(layout))
    return tmp_path


def test_a_kind_added_to_a_scratch_layout_is_accepted_by_the_table(tmp_path, empty_db):
    """Goes through the generator and the runner and never through the library."""
    root = scratch_root(tmp_path)
    subprocess.run([sys.executable, "-m", "wdf_artifactindex.gen", "--root", str(root)], check=True)
    shutil.copy(MIGRATIONS / "0300_artifact_index.sql", root)
    shutil.copy(root / KINDS_SQL, root)
    apply(empty_db, from_dirs([root]))
    with psycopg.connect(empty_db) as conn:
        insert(conn, reference_row("scratch-blob"))
        insert(conn, reference_row("scratch-run", **attempt_producer()))
        refused(conn, reference_row("scratch-blob", 1, **attempt_producer()))
        refused(conn, reference_row("scratch-run", 1))
        refused(conn, reference_row("scratch-other"))
        # The original kinds keep their rule.
        refused(conn, reference_row("result", 2))


def release_dir(tmp_path: Path, *, previous: bool) -> Path:
    """The migrations of this release, or of the release before the use links."""
    root = tmp_path / ("previous" if previous else "current")
    root.mkdir()
    for item in MIGRATIONS.iterdir():
        if item.name.endswith(".sql") and not (previous and item.name.startswith("0301")):
            (root / item.name).write_text(item.read_text())
    return root


def test_from_empty_and_from_previous_release(tmp_path, empty_db, appdb_server):
    """Every statement of the new migration is expand only, so the runner takes it with no flags."""
    current = from_dirs([release_dir(tmp_path, previous=False)])
    assert [m.name for m in current].count("0301_artifact_use.sql") == 1
    assert apply(empty_db, current) == [m.name for m in current]
    assert apply(empty_db, current) == []

    empty_previous = f"{empty_db}_previous"
    admin = psycopg.connect(empty_db.rsplit("/", 1)[0] + "/postgres", autocommit=True)
    admin.execute(f'CREATE DATABASE "{empty_previous.rsplit("/", 1)[1]}"')
    previous = from_dirs([release_dir(tmp_path, previous=True)])
    assert "0301_artifact_use.sql" not in [m.name for m in previous]
    apply(empty_previous, previous)
    with psycopg.connect(empty_previous) as conn:
        insert(conn, reference_row("component", uri="s3://wdf-test/wasm", key="wasm"))
        insert(conn, cache_row("s3://wdf-test/wasm"))
        insert(conn, reference_row("source", 1, deleted_at="2026-11-02T00:00:00Z"))
    assert apply(empty_previous, current) == ["0301_artifact_use.sql"]
    with psycopg.connect(empty_previous) as conn:
        rows = conn.execute(
            "SELECT kind, build_info_uri, purged_at FROM artifact ORDER BY row_id"
        ).fetchall()
        assert rows == [
            ("component", None, None),
            ("compile-cache", None, None),
            ("source", None, None),
        ]
        assert conn.execute("SELECT count(*) FROM artifact_use").fetchone() == (0,)
        refused(conn, reference_row("source", 2, build_info_uri="s3://wdf-test/wasm"))
        refused(conn, reference_row("source", 3, purged_at="2026-11-02T00:00:00Z"))
        refused(
            conn,
            cache_row(
                "s3://wdf-test/wasm",
                key="b" * 64,
                deleted_at=None,
                purged_at="2026-11-02T00:00:00Z",
            ),
        )
