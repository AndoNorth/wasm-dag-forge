"""Guarded writes of the accepted_job row.

Every function takes an open connection and never commits, so the caller composes a row change
and its events in one transaction.
"""

from dataclasses import dataclass
from functools import cache

import yaml
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import ACTIONS, CONTRACT, JobRow

# The columns a caller writes at insert. The rest is set by the database or by a guarded change.
INSERT_COLUMNS = (
    "job_id",
    "tenant",
    "submission_key",
    "batch_id",
    "priority_class",
    "job_class",
    "manifest_ref",
    "schema_version",
    "traceparent",
    "tracestate",
    "seed",
    "parameters",
    "fingerprint",
)
JSON_COLUMNS = {"manifest_ref", "parameters"}
READ_COLUMNS = ", ".join(
    f"{name}::text AS {name}" if name in ("job_id", "batch_id") else name
    for name in JobRow.model_fields
)


class IllegalTransition(ValueError):
    """A move that is not in the transition table, or a row-only move taken without the flag."""


class JobNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Vocabulary:
    statuses: tuple[str, ...]
    terminal: frozenset[str]
    # Each allowed move (from, to) mapped to whether only a job that never started may take it.
    transitions: dict[tuple[str, str], bool]


@dataclass(frozen=True)
class Duplicate:
    """The row that already holds the key of a rejected insert."""

    job_id: str
    status: str
    fingerprint: str


@dataclass(frozen=True)
class InsertResult:
    inserted: list[str]
    duplicates: list[Duplicate]


@dataclass(frozen=True)
class Moved:
    status: str


@dataclass(frozen=True)
class Lost:
    """Another writer moved the row first, and `status` is the one it holds now."""

    status: str


@dataclass(frozen=True)
class ControlRequest:
    action: str
    control_seq: int


@cache
def status_vocabulary() -> Vocabulary:
    """The statuses, the terminal set and the transition table, read from the status contract."""
    contract = yaml.safe_load((CONTRACT / "status.yaml").read_text())
    return Vocabulary(
        statuses=tuple(contract["statuses"]),
        terminal=frozenset(contract["terminal"]),
        transitions={
            (t["from"], t["to"]): t.get("row_only", False) for t in contract["transitions"]
        },
    )


def _params(row: JobRow) -> dict:
    values = row.model_dump(include=set(INSERT_COLUMNS))
    return {k: Jsonb(v) if k in JSON_COLUMNS else v for k, v in values.items()}


def insert_accepted(conn: Connection, rows: list[JobRow]) -> InsertResult:
    """Inserts each row at `accepted` and reports each key that already exists."""
    columns = ", ".join(INSERT_COLUMNS)
    marks = ", ".join(f"%({name})s" for name in INSERT_COLUMNS)
    sql = (
        f"INSERT INTO accepted_job ({columns}) VALUES ({marks})"
        " ON CONFLICT (tenant, submission_key) DO NOTHING RETURNING job_id::text"
    )
    inserted: list[str] = []
    duplicates: list[Duplicate] = []
    with conn.cursor(row_factory=dict_row) as cur:
        for row in rows:
            cur.execute(sql, _params(row))
            if won := cur.fetchone():
                inserted.append(won["job_id"])
                continue
            cur.execute(
                "SELECT job_id::text AS job_id, status, fingerprint FROM accepted_job"
                " WHERE tenant = %s AND submission_key = %s",
                (row.tenant, row.submission_key),
            )
            duplicates.append(Duplicate(**cur.fetchone()))
    return InsertResult(inserted, duplicates)


def get_job(conn: Connection, job_id: str) -> JobRow | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {READ_COLUMNS} FROM accepted_job WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
    return JobRow(**row) if row else None


def _applied_actions(expected: str, to: str) -> list[str]:
    """The requested actions that a move completes, which stop being outstanding."""
    if to in status_vocabulary().terminal:
        return list(ACTIONS)
    if to == "paused":
        return ["pause"]
    if expected == "resume-pending" and to in ("running", "accepted"):
        return ["resume"]
    return []


def _status_of(conn: Connection, job_id: str) -> str:
    found = conn.execute("SELECT status FROM accepted_job WHERE job_id = %s", (job_id,)).fetchone()
    if found is None:
        raise JobNotFound(job_id)
    return found[0]


def transition(
    conn: Connection, job_id: str, expected: str, to: str, row_only: bool = False
) -> Moved | Lost:
    """Moves the row from `expected` to `to` when it still holds `expected`, and loses otherwise.

    A move outside the table raises, and so does a row-only move without the flag, which the caller
    sets only for a job that never started.
    """
    vocabulary = status_vocabulary()
    if (expected, to) not in vocabulary.transitions:
        raise IllegalTransition(f"{expected} to {to} is not in the transition table")
    if vocabulary.transitions[(expected, to)] and not row_only:
        raise IllegalTransition(f"{expected} to {to} is for a job that never started")
    moved = conn.execute(
        "UPDATE accepted_job SET status = %(to)s,"
        " terminal_at = CASE WHEN %(terminal)s THEN now() ELSE terminal_at END,"
        " requested_action = CASE WHEN requested_action = ANY(%(applied)s) THEN NULL"
        " ELSE requested_action END"
        " WHERE job_id = %(job_id)s AND status = %(expected)s",
        {
            "to": to,
            "terminal": to in vocabulary.terminal,
            "applied": _applied_actions(expected, to),
            "job_id": job_id,
            "expected": expected,
        },
    )
    if moved.rowcount:
        return Moved(to)
    return Lost(_status_of(conn, job_id))


def request_action(conn: Connection, job_id: str, action: str) -> ControlRequest:
    """Records a requested action.

    Repeating the outstanding action returns its sequence, and a different one takes the next.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    taken = conn.execute(
        "UPDATE accepted_job SET requested_action = %s, control_seq = control_seq + 1,"
        " control_delivered_at = NULL"
        " WHERE job_id = %s AND requested_action IS DISTINCT FROM %s RETURNING control_seq",
        (action, job_id, action),
    ).fetchone()
    if taken:
        return ControlRequest(action, taken[0])
    found = conn.execute(
        "SELECT control_seq FROM accepted_job WHERE job_id = %s", (job_id,)
    ).fetchone()
    if found is None:
        raise JobNotFound(job_id)
    return ControlRequest(action, found[0])


def mark_published(conn: Connection, job_id: str) -> None:
    """Records the broker receipt once, and a repeat keeps the first time."""
    conn.execute(
        "UPDATE accepted_job SET published_at = now() WHERE job_id = %s AND published_at IS NULL",
        (job_id,),
    )
    _status_of(conn, job_id)


def mark_delivered(conn: Connection, job_id: str, control_seq: int) -> None:
    """Records that the request of `control_seq` was delivered, and the action stays outstanding."""
    conn.execute(
        "UPDATE accepted_job SET control_delivered_at = now()"
        " WHERE job_id = %s AND control_seq = %s AND requested_action IS NOT NULL"
        " AND control_delivered_at IS NULL",
        (job_id, control_seq),
    )
    _status_of(conn, job_id)
