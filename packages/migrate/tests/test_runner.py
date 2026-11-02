"""The runner against a real Postgres, with migration sets written to a temporary directory."""

import threading
import time

import psycopg
import pytest
from wdf_migrate import LOCK_KEY, Refused, apply, check, discover, status
from wdf_migrate.__main__ import main
from wdf_migrate.runner import Migration, from_dirs, lint

pytestmark = pytest.mark.integration

V1 = {
    "0001_item.sql": "CREATE TABLE item (id bigint PRIMARY KEY, label text);",
    "0002_item_note.sql": "ALTER TABLE item ADD COLUMN note text;",
}
V2 = {
    **V1,
    "0003_item_kind.sql": "ALTER TABLE item ADD COLUMN kind text NOT NULL DEFAULT 'a';",
    "R__item_checks.sql": (
        "ALTER TABLE item DROP CONSTRAINT IF EXISTS item_label_len;"
        " ALTER TABLE item ADD CONSTRAINT item_label_len CHECK (length(label) < 50);"
    ),
}


def source(tmp_path, files, name="src"):
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    for file, sql in files.items():
        (directory / file).write_text(sql)
    return from_dirs([directory])


def shape(dsn):
    with psycopg.connect(dsn) as db:
        columns = db.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = 'public'"
            " AND table_name <> 'schema_migration' ORDER BY 1, 2"
        ).fetchall()
        constraints = db.execute(
            "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)"
            " FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
        ).fetchall()
    return columns, constraints


def test_from_empty_and_from_previous_release_reach_the_same_schema(
    tmp_path, empty_db, appdb_server
):
    from wdf_migrate.fixtures import PASSWORD, _connect

    with _connect(appdb_server) as admin:
        admin.execute("CREATE DATABASE upgraded")
    upgraded = f"postgresql://postgres:{PASSWORD}@127.0.0.1:{appdb_server}/upgraded"
    apply(upgraded, source(tmp_path, V1, "v1"))
    apply(upgraded, source(tmp_path, V2, "v2"))
    apply(empty_db, source(tmp_path, V2, "v2"))
    assert shape(upgraded) == shape(empty_db)
    assert shape(empty_db)[0]


def test_applying_twice_is_a_noop(tmp_path, empty_db):
    migrations = source(tmp_path, V2)
    assert len(apply(empty_db, migrations)) == 4
    assert apply(empty_db, migrations) == []
    assert {state for _, state in status(empty_db, migrations)} == {"applied"}


def test_changed_applied_file_is_refused(tmp_path, empty_db):
    apply(empty_db, source(tmp_path, V1))
    edited = source(tmp_path, {**V1, "0002_item_note.sql": V1["0002_item_note.sql"] + " "})
    with pytest.raises(Refused, match="edited after it was applied"):
        apply(empty_db, edited)
    assert ("0002_item_note.sql", "changed") in status(empty_db, edited)
    assert main(["check", "--dsn", empty_db, "--source", str(tmp_path / "src")]) == 1


def test_check_exits_3_when_the_database_is_unreachable(tmp_path):
    source(tmp_path, V1)
    dsn = "postgresql://postgres@127.0.0.1:1/none?connect_timeout=2"
    assert main(["check", "--dsn", dsn, "--source", str(tmp_path / "src")]) == 3


def test_usage_error_exits_2():
    with pytest.raises(SystemExit) as stop:
        main(["apply"])
    assert stop.value.code == 2


def test_two_runners_serialize_on_the_lock(tmp_path, empty_db):
    migrations = source(tmp_path, V1)
    holder = psycopg.connect(empty_db, autocommit=True)
    holder.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    finished = []
    runner = threading.Thread(target=lambda: finished.append(apply(empty_db, migrations)))
    runner.start()
    time.sleep(1.5)
    assert runner.is_alive()
    holder.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    runner.join(30)
    assert finished == [["0001_item.sql", "0002_item_note.sql"]]
    holder.close()


def test_destructive_statement_needs_the_contract_suffix(tmp_path, empty_db):
    apply(empty_db, source(tmp_path, V1))
    drop = "ALTER TABLE item DROP COLUMN note;"
    with pytest.raises(Refused, match="DROP COLUMN"):
        apply(empty_db, source(tmp_path, {**V1, "0003_drop_note.sql": drop}, "plain"))
    contract = source(tmp_path, {**V1, "0003_drop_note.contract.sql": drop}, "contract")
    with pytest.raises(Refused, match="DROP COLUMN"):
        apply(empty_db, contract)
    assert apply(empty_db, contract, allow_contract=True) == ["0003_drop_note.contract.sql"]
    assert main(["status", "--dsn", empty_db, "--source", str(tmp_path / "contract")]) == 0


def test_repeatable_file_reapplies_when_its_checksum_changes(tmp_path, empty_db):
    first = {**V1, "R__view.sql": "CREATE OR REPLACE VIEW item_ids AS SELECT id FROM item;"}
    assert apply(empty_db, source(tmp_path, first)) == [*V1, "R__view.sql"]
    second = {**V1, "R__view.sql": "CREATE OR REPLACE VIEW item_ids AS SELECT id, label FROM item;"}
    assert apply(empty_db, source(tmp_path, second)) == ["R__view.sql"]
    with psycopg.connect(empty_db) as db:
        assert db.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'item_ids'"
        ).fetchone() == (2,)


def test_repeatable_file_may_replace_its_own_constraint(tmp_path, empty_db):
    check_sql = "ALTER TABLE item DROP CONSTRAINT IF EXISTS item_label_len; {}"
    add = "ALTER TABLE item ADD CONSTRAINT item_label_len CHECK (length(label) < {});"
    for limit in (5, 10):
        files = {**V1, "R__limit.sql": check_sql.format(add.format(limit))}
        apply(empty_db, source(tmp_path, files))
    with psycopg.connect(empty_db) as db:
        (definition,) = db.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'item_label_len'"
        ).fetchone()
    assert "10" in definition


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE item;",
        "ALTER TABLE item DROP COLUMN note;",
        "ALTER TABLE item RENAME TO thing;",
        "ALTER TABLE item ALTER COLUMN note TYPE int;",
    ],
)
def test_repeatable_file_refuses_dropping_a_table(sql):
    with pytest.raises(Refused):
        lint(Migration("R__bad.sql", sql), allow_contract=True)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE item;",
        "ALTER TABLE item RENAME COLUMN a TO b;",
        "ALTER TABLE item ALTER COLUMN a SET DATA TYPE int;",
        "ALTER TABLE item ALTER COLUMN a SET NOT NULL;",
    ],
)
def test_numbered_file_refuses_what_is_not_expand_only(sql):
    with pytest.raises(Refused):
        lint(Migration("0009_bad.sql", sql))


def test_lint_ignores_comments_strings_and_function_bodies():
    sql = (
        "-- DROP TABLE a;\nSELECT 'DROP TABLE b';\n/* RENAME */\n"
        "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $$ SELECT 1 -- DROP TABLE c\n$$;"
    )
    lint(Migration("0009_ok.sql", sql))


def test_appdb_has_every_installed_migration_applied(appdb):
    assert {s for _, s in status(appdb, discover())} <= {"applied"}


def test_check_does_not_change_the_database(tmp_path, empty_db):
    check(empty_db, source(tmp_path, V1))
    with psycopg.connect(empty_db) as db:
        assert db.execute("SELECT to_regclass('schema_migration')").fetchone() == (None,)
