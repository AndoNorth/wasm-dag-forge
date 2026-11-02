"""Applies the numbered and repeatable SQL files of the installed table packages in order.

Numbered files `<NNNN>_<name>.sql` run once, in name order, and are expand-only.
Repeatable files `R__<name>.sql` run after every numbered file whenever their checksum changes.
A numbered file named `*.contract.sql` may contract the schema when the run allows it.
"""

import hashlib
import re
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path

import psycopg

ENTRY_POINT_GROUP = "wdf.migrations"
LOCK_KEY = 0x7764666D  # "wdfm"

NUMBERED = re.compile(r"^(\d{4})_[a-z0-9_]+(\.contract)?\.sql$")
REPEATABLE = re.compile(r"^R__[a-z0-9_]+\.sql$")

# Comments, string literals and dollar-quoted bodies hold text that is not a statement.
NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|(\$[A-Za-z_]*\$).*?\1", re.DOTALL)
DROP = re.compile(r"\bDROP\s+(?:IF\s+EXISTS\s+)?(\w+)", re.IGNORECASE)
RENAME = re.compile(r"\bRENAME\b", re.IGNORECASE)
RETYPE = re.compile(r"\bALTER\s+(?:COLUMN\s+)?\w+\s+(?:SET\s+DATA\s+)?TYPE\b", re.IGNORECASE)
SET_NOT_NULL = re.compile(r"\bSET\s+NOT\s+NULL\b", re.IGNORECASE)
REPLACEABLE = {"constraint", "trigger", "function", "policy"}


class Refused(Exception):
    """A change the runner will not apply: a destructive statement or an edited applied file."""


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str

    @property
    def repeatable(self) -> bool:
        return self.name.startswith("R__")

    @property
    def contract(self) -> bool:
        return self.name.endswith(".contract.sql")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def _collect(files) -> list[Migration]:
    found: dict[str, Migration] = {}
    for name, text in files:
        if not name.endswith(".sql"):
            continue
        if not (NUMBERED.match(name) or REPEATABLE.match(name)):
            raise Refused(f"{name}: not a numbered or repeatable migration name")
        if name in found:
            raise Refused(f"{name}: provided by more than one source")
        found[name] = Migration(name, text)
    numbers: dict[str, str] = {}
    for name in found:
        if match := NUMBERED.match(name):
            if other := numbers.setdefault(match.group(1), name):
                if other != name:
                    raise Refused(f"{name} and {other} share the number {match.group(1)}")
    numbered = sorted((m for m in found.values() if not m.repeatable), key=lambda m: m.name)
    repeatable = sorted((m for m in found.values() if m.repeatable), key=lambda m: m.name)
    return [*numbered, *repeatable]


def discover() -> list[Migration]:
    """The migrations of every installed package that registers one under `wdf.migrations`."""
    files = []
    for entry in metadata.entry_points(group=ENTRY_POINT_GROUP):
        for item in resources.files(entry.value).iterdir():
            if item.is_file() and item.name.endswith(".sql"):
                files.append((item.name, item.read_text()))
    return _collect(files)


def from_dirs(dirs: list[Path]) -> list[Migration]:
    return _collect(
        (p.name, p.read_text())
        for d in dirs
        for p in Path(d).iterdir()
        if p.is_file() and p.name.endswith(".sql")
    )


def lint(migration: Migration, allow_contract: bool = False) -> None:
    """Raises Refused for a statement that is not expand-only, unless the run allows it."""
    if migration.contract and allow_contract and not migration.repeatable:
        return
    sql = NOISE.sub(" ", migration.sql)
    for word in DROP.findall(sql):
        if migration.repeatable and word.lower() in REPLACEABLE:
            continue
        raise Refused(f"{migration.name}: DROP {word.upper()} is not expand-only")
    for pattern, what in ((RENAME, "RENAME"), (RETYPE, "a type change")):
        if pattern.search(sql):
            raise Refused(f"{migration.name}: {what} is not expand-only")
    if not migration.repeatable and SET_NOT_NULL.search(sql):
        raise Refused(f"{migration.name}: SET NOT NULL is not expand-only")


def _applied(db: psycopg.Connection) -> dict[str, str]:
    if db.execute("SELECT to_regclass('schema_migration')").fetchone()[0] is None:
        return {}
    return dict(db.execute("SELECT name, checksum FROM schema_migration").fetchall())


def _plan(
    db: psycopg.Connection, migrations: list[Migration], allow_contract: bool
) -> list[Migration]:
    """The migrations to run, refusing before any change when one cannot."""
    applied = _applied(db)
    sources = {m.name for m in migrations}
    for name in applied.keys() - sources:
        raise Refused(f"{name}: applied but no longer provided")
    pending = []
    for m in migrations:
        recorded = applied.get(m.name)
        if recorded == m.checksum:
            continue
        if recorded is not None and not m.repeatable:
            raise Refused(f"{m.name}: edited after it was applied")
        lint(m, allow_contract)
        pending.append(m)
    return pending


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def apply(dsn: str, migrations: list[Migration], allow_contract: bool = False) -> list[str]:
    """Applies what is pending, one transaction per file, and returns the names applied."""
    done = []
    with _connect(dsn) as db:
        db.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration ("
                " name text PRIMARY KEY, checksum text NOT NULL,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for m in _plan(db, migrations, allow_contract):
                with db.transaction():
                    db.execute(m.sql)
                    db.execute(
                        "INSERT INTO schema_migration (name, checksum) VALUES (%s, %s)"
                        " ON CONFLICT (name) DO UPDATE"
                        " SET checksum = EXCLUDED.checksum, applied_at = now()",
                        (m.name, m.checksum),
                    )
                done.append(m.name)
        finally:
            db.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return done


def status(dsn: str, migrations: list[Migration]) -> list[tuple[str, str]]:
    """Each migration as `applied`, `pending` or `changed` (an edited file)."""
    with _connect(dsn) as db:
        applied = _applied(db)
    return [
        (
            m.name,
            "pending"
            if m.name not in applied
            else "applied"
            if applied[m.name] == m.checksum
            else "changed",
        )
        for m in migrations
    ]


def check(dsn: str, migrations: list[Migration], allow_contract: bool = False) -> None:
    """Raises Refused when apply would, without changing anything."""
    with _connect(dsn) as db:
        _plan(db, migrations, allow_contract)
