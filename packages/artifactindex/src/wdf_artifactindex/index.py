"""The artifact index library.

Every function takes an open connection and never commits, so the caller composes an index change
with the rest of its transaction. The guards against a pinned wasm assume READ COMMITTED, the
default, because the second statement of `mark_deleted` must see what committed while it waited.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from psycopg.rows import dict_row, tuple_row
from pydantic import BaseModel, ConfigDict, Field

from .models import HASH_PATTERN, ArtifactRow, ArtifactUse
from .reference import split_uri, validate


@dataclass(frozen=True)
class Indexed:
    row_id: int


@dataclass(frozen=True)
class Existing:
    row_id: int


@dataclass(frozen=True)
class Revived:
    """A soft deleted or purged row was made live again."""

    row_id: int


@dataclass(frozen=True)
class Linked:
    row_id: int


@dataclass(frozen=True)
class RowPurged:
    """The row was purged, so nothing was linked, and the caller puts the object again."""


@dataclass(frozen=True)
class Purged:
    """The row now records `purged_at`, and the caller removes the object before it commits."""


@dataclass(frozen=True)
class Deleted:
    row_id: int


@dataclass(frozen=True)
class Pinned:
    """A live cache entry names the object as its wasm or build-info, so nothing changed."""


@dataclass(frozen=True)
class Skipped:
    """The row is referenced, too young or in the wrong state, so nothing changed."""


@dataclass(frozen=True)
class Invalidated:
    row_id: int


@dataclass(frozen=True)
class NoEntry:
    """No live cache entry holds the key, so nothing changed."""


@dataclass(frozen=True)
class Unreferenced:
    row_id: int
    uri: str
    deleted_at: datetime | None


@dataclass(frozen=True)
class Won:
    row_id: int


@dataclass(frozen=True)
class Lost:
    """Another entry already holds the cache key, so nothing changed."""


@dataclass(frozen=True)
class WasmGone:
    """The wasm or build-info row is missing or soft deleted, so nothing changed."""


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_key: str = Field(pattern=HASH_PATTERN)
    wasm_uri: str = Field(min_length=1)
    componentize_py_version: str = Field(min_length=1)
    wit_version: str = Field(min_length=1)
    build_info_uri: str | None = Field(default=None, min_length=1)
    last_used_at: datetime | None = Field(
        default=None, description="The database clock when absent."
    )


def _one(conn: Any, sql: str, params: Any) -> tuple | None:
    with conn.cursor(row_factory=tuple_row) as cur:
        return cur.execute(sql, params).fetchone()


def index_reference(conn: Any, reference: Any) -> Indexed | Existing | Revived:
    """Stores a validated reference once, making a soft deleted or purged row live again.

    A uri that is already live is `Existing`. The revive runs under the row lock, so it is ordered
    with a purge that is deciding at the same moment.
    """
    ref = validate(reference)
    bucket, key = split_uri(ref.uri)
    producer = getattr(ref, "producer", None)
    row = _one(
        conn,
        "INSERT INTO artifact (kind, uri, bucket, key, sha256, size, name,"
        " producer_job, producer_task, producer_attempt)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (uri) DO NOTHING RETURNING row_id",
        (
            ref.kind,
            ref.uri,
            bucket,
            key,
            ref.sha256,
            ref.size,
            ref.name,
            producer and producer.job,
            producer and producer.task,
            producer and producer.attempt,
        ),
    )
    if row:
        return Indexed(row[0])
    row_id, deleted_at = _one(
        conn, "SELECT row_id, deleted_at FROM artifact WHERE uri = %s FOR NO KEY UPDATE", (ref.uri,)
    )
    if deleted_at is None:
        return Existing(row_id)
    conn.execute(
        "UPDATE artifact SET deleted_at = NULL, purged_at = NULL WHERE row_id = %s", (row_id,)
    )
    return Revived(row_id)


def mark_deleted(conn: Any, uri: str, at: datetime) -> Deleted | Pinned:
    """Soft deletes a row, keeping it, unless a live cache entry names it as its wasm or build-info.

    The row lock comes first and the guarded update is a statement of its own, because a single
    statement would not see a cache entry that committed while it waited for the lock.
    """
    if _one(conn, "SELECT 1 FROM artifact WHERE uri = %s FOR NO KEY UPDATE", (uri,)) is None:
        raise LookupError(f"no artifact row for {uri}")
    row = _one(
        conn,
        "UPDATE artifact SET deleted_at = COALESCE(deleted_at, %(at)s) WHERE uri = %(uri)s"
        " AND NOT EXISTS (SELECT 1 FROM artifact c WHERE c.kind = 'compile-cache'"
        " AND %(uri)s IN (c.wasm_uri, c.build_info_uri) AND c.deleted_at IS NULL)"
        " RETURNING row_id",
        {"uri": uri, "at": at},
    )
    return Deleted(row[0]) if row else Pinned()


def references_to(conn: Any, uri: str) -> list[str]:
    """The cache keys of the live cache entries that name the uri as their wasm or build-info."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT cache_key FROM artifact WHERE %(uri)s IN (wasm_uri, build_info_uri)"
            " AND deleted_at IS NULL ORDER BY row_id",
            {"uri": uri},
        )
        return [row[0] for row in cur.fetchall()]


def record_cache_entry(conn: Any, entry: Any) -> Won | Lost | WasmGone:
    """Writes a cache entry for a live wasm and build-info, the first writer of a key winning.

    The check of the objects, their `FOR SHARE` lock and the insert are one statement, so a soft
    delete that commits first is seen and one that comes later waits for this transaction.
    """
    e = entry if isinstance(entry, CacheEntry) else CacheEntry.model_validate(entry)
    live, row_id = _one(
        conn,
        "WITH pinned AS ("
        " SELECT uri FROM artifact WHERE uri IN (%(wasm_uri)s, %(build_info_uri)s)"
        " AND deleted_at IS NULL ORDER BY uri FOR SHARE"
        "), complete AS ("
        " SELECT count(*) = CASE WHEN %(build_info_uri)s::text IS NULL THEN 1 ELSE 2 END AS ok"
        " FROM pinned"
        "), won AS ("
        " INSERT INTO artifact (kind, cache_key, wasm_uri, build_info_uri, componentize_py_version,"
        " wit_version, last_used_at)"
        " SELECT 'compile-cache', %(cache_key)s, %(wasm_uri)s, %(build_info_uri)s,"
        " %(componentize_py_version)s, %(wit_version)s, COALESCE(%(last_used_at)s, now())"
        " WHERE (SELECT ok FROM complete)"
        " ON CONFLICT (cache_key) WHERE kind = 'compile-cache' DO NOTHING RETURNING row_id"
        ") SELECT (SELECT ok FROM complete), (SELECT row_id FROM won)",
        e.model_dump(),
    )
    if not live:
        return WasmGone()
    return Won(row_id) if row_id is not None else Lost()


def touch_cache_entry(conn: Any, key: str, at: datetime) -> None:
    """Moves the last use of a cache entry forward, never back."""
    conn.execute(
        "UPDATE artifact SET last_used_at = GREATEST(last_used_at, %s)"
        " WHERE kind = 'compile-cache' AND cache_key = %s",
        (at, key),
    )


def lookup_cache_entry(conn: Any, key: str) -> CacheEntry | None:
    """The live cache entry of the key, or None. A retired key never matches."""
    row = _one(
        conn,
        "SELECT cache_key, wasm_uri, componentize_py_version, wit_version, build_info_uri,"
        " last_used_at FROM artifact WHERE kind = 'compile-cache' AND cache_key = %s"
        " AND deleted_at IS NULL",
        (key,),
    )
    return row and CacheEntry(
        cache_key=row[0],
        wasm_uri=row[1],
        componentize_py_version=row[2],
        wit_version=row[3],
        build_info_uri=row[4],
        last_used_at=row[5],
    )


_RETIRE = (
    "deleted_at = %(at)s, cache_key = encode("
    "sha256(convert_to(a.cache_key || ':' || a.row_id::text, 'UTF8')), 'hex')"
)


def invalidate_cache_entry(conn: Any, key: str, at: datetime) -> Invalidated | NoEntry:
    """Soft deletes the live entry of the key and gives it a retired key, so the key is free.

    The retired key is the SHA-256 hex of `old_key:row_id`, which keeps the table's checks true and
    the unique index untouched.
    """
    row = _one(
        conn,
        f"UPDATE artifact a SET {_RETIRE} WHERE a.kind = 'compile-cache' AND a.cache_key = %(key)s"
        " AND a.deleted_at IS NULL RETURNING a.row_id",
        {"key": key, "at": at},
    )
    return Invalidated(row[0]) if row else NoEntry()


def expired_cache_entries(
    conn: Any, before: datetime, at: datetime, limit: int | None = None
) -> list[str]:
    """Retires the live entries last used before the cutoff and returns their original keys.

    Rows another session holds are left for the next run, so a run never waits on a lookup.
    """
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "WITH due AS (SELECT row_id, cache_key FROM artifact WHERE kind = 'compile-cache'"
            " AND deleted_at IS NULL AND last_used_at < %(before)s ORDER BY row_id LIMIT %(limit)s"
            " FOR NO KEY UPDATE SKIP LOCKED)"
            f" UPDATE artifact a SET {_RETIRE} FROM due WHERE a.row_id = due.row_id"
            " RETURNING due.cache_key",
            {"before": before, "at": at, "limit": limit},
        )
        return sorted(row[0] for row in cur.fetchall())


def link_use(conn: Any, job_id: str, uri: str, at: datetime) -> Linked | Revived | RowPurged:
    """Records that the job holds the blob, under the row lock, and renews a use already there.

    A tombstoned row is made live again, a purged row is refused with `RowPurged` and a missing one
    raises LookupError. A retried submission reuses its job id, so it renews the protection.
    """
    row = _one(
        conn,
        "SELECT row_id, deleted_at, purged_at FROM artifact WHERE uri = %s FOR NO KEY UPDATE",
        (uri,),
    )
    if row is None:
        raise LookupError(f"no artifact row for {uri}")
    row_id, deleted_at, purged_at = row
    if purged_at is not None:
        return RowPurged()
    conn.execute(
        "INSERT INTO artifact_use (job_id, artifact_row_id, created_at, last_linked_at)"
        " VALUES (%(job)s, %(row)s, %(at)s, %(at)s) ON CONFLICT (job_id, artifact_row_id)"
        " DO UPDATE SET last_linked_at = GREATEST(artifact_use.last_linked_at, %(at)s)",
        {"job": job_id, "row": row_id, "at": at},
    )
    if deleted_at is None:
        return Linked(row_id)
    conn.execute("UPDATE artifact SET deleted_at = NULL WHERE row_id = %s", (row_id,))
    return Revived(row_id)


def uses_of(conn: Any, uri: str) -> list[ArtifactUse]:
    """The uses of the blob, oldest first."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT u.job_id::text, u.artifact_row_id, u.created_at, u.last_linked_at"
            " FROM artifact_use u JOIN artifact a ON a.row_id = u.artifact_row_id"
            " WHERE a.uri = %s ORDER BY u.created_at, u.job_id",
            (uri,),
        )
        return [
            ArtifactUse(job_id=r[0], artifact_row_id=r[1], created_at=r[2], last_linked_at=r[3])
            for r in cur.fetchall()
        ]


def unreferenced_content(
    conn: Any, at: datetime, grace: timedelta, uri: str | None = None, limit: int | None = None
) -> list[Unreferenced]:
    """The content-addressed rows, live or tombstoned and not purged, that nothing holds.

    A row is held by a live cache entry (as wasm or build-info), by a use row unless its job row
    exists and is terminal, and by a non-terminal job row as its manifest or pending patch. The
    use row of a job that has no row yet holds for the grace counted from its last link. A row is
    returned only when it is older than the grace. Naming a uri asks the same question of one row.
    """
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT a.row_id, a.uri, a.deleted_at FROM artifact a"
            " WHERE a.kind <> 'compile-cache' AND a.producer_job IS NULL AND a.purged_at IS NULL"
            " AND (%(uri)s::text IS NULL OR a.uri = %(uri)s)"
            " AND a.created_at <= %(at)s - %(grace)s"
            " AND NOT EXISTS (SELECT 1 FROM artifact c WHERE c.kind = 'compile-cache'"
            " AND c.deleted_at IS NULL AND (c.wasm_uri = a.uri OR c.build_info_uri = a.uri))"
            " AND NOT EXISTS (SELECT 1 FROM artifact_use u"
            " LEFT JOIN accepted_job j ON j.job_id = u.job_id"
            " LEFT JOIN job_status s ON s.status = j.status"
            " WHERE u.artifact_row_id = a.row_id AND"
            " CASE WHEN j.job_id IS NULL THEN u.last_linked_at > %(at)s - %(grace)s"
            " ELSE NOT coalesce(s.terminal, false) END)"
            " AND NOT EXISTS (SELECT 1 FROM accepted_job j JOIN job_status s ON s.status = j.status"
            " WHERE NOT s.terminal AND (j.manifest_ref ->> 'uri' = a.uri"
            " OR j.pending_patch_ref ->> 'uri' = a.uri))"
            " ORDER BY a.row_id LIMIT %(limit)s",
            {"at": at, "grace": grace, "uri": uri, "limit": limit},
        )
        return [Unreferenced(*row) for row in cur.fetchall()]


def tombstone(conn: Any, uri: str, at: datetime, grace: timedelta) -> Deleted | Skipped:
    """Marks a live row deleted when, under its lock, nothing holds it any longer.

    A reference that arrives later revives the row, so a tombstone never loses one.
    """
    if _one(conn, "SELECT 1 FROM artifact WHERE uri = %s FOR NO KEY UPDATE", (uri,)) is None:
        raise LookupError(f"no artifact row for {uri}")
    held = unreferenced_content(conn, at, grace, uri)
    if not held or held[0].deleted_at is not None:
        return Skipped()
    conn.execute("UPDATE artifact SET deleted_at = %s WHERE row_id = %s", (at, held[0].row_id))
    return Deleted(held[0].row_id)


def purge(
    conn: Any, uri: str, at: datetime, tombstone_interval: timedelta, grace: timedelta
) -> Purged | Skipped:
    """Records `purged_at` for a row still tombstoned past the interval and still unreferenced.

    The row lock is held until the caller's transaction ends, so the caller removes the object
    after this returns and before it commits, and a link or a revive that waits sees the result.
    """
    if _one(conn, "SELECT 1 FROM artifact WHERE uri = %s FOR NO KEY UPDATE", (uri,)) is None:
        raise LookupError(f"no artifact row for {uri}")
    held = unreferenced_content(conn, at, grace, uri)
    if not held or held[0].deleted_at is None or held[0].deleted_at > at - tombstone_interval:
        return Skipped()
    conn.execute("UPDATE artifact SET purged_at = %s WHERE row_id = %s", (at, held[0].row_id))
    return Purged()


def _checkpoints(conn: Any, where: str, params: dict) -> list[ArtifactRow]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT row_id, kind, uri, bucket, key, sha256, size, name, producer_job::text,"
            " producer_task, producer_attempt, created_at, deleted_at, cache_key, wasm_uri,"
            " componentize_py_version, wit_version, last_used_at, build_info_uri, purged_at"
            f" FROM artifact WHERE kind = 'checkpoint' AND deleted_at IS NULL AND {where}"
            " ORDER BY row_id DESC",
            params,
        )
        return [ArtifactRow.model_validate(row) for row in cur.fetchall()]


def checkpoints_of(conn: Any, job_id: str, task: str) -> list[ArtifactRow]:
    """The live checkpoint references of a task of a job, newest first."""
    return _checkpoints(
        conn, "producer_job = %(job)s AND producer_task = %(task)s", {"job": job_id, "task": task}
    )


def previous_checkpoint(conn: Any, uri: str) -> ArtifactRow | None:
    """The newest live checkpoint of the same task that was indexed before the named one."""
    older = _checkpoints(
        conn,
        "(producer_job, producer_task) = (SELECT producer_job, producer_task FROM artifact"
        " WHERE uri = %(uri)s) AND row_id < (SELECT row_id FROM artifact WHERE uri = %(uri)s)",
        {"uri": uri},
    )
    return older[0] if older else None
