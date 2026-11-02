"""The artifact index library.

Every function takes an open connection and never commits, so the caller composes an index change
with the rest of its transaction. The guards against a pinned wasm assume READ COMMITTED, the
default, because the second statement of `mark_deleted` must see what committed while it waited.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import tuple_row
from pydantic import BaseModel, ConfigDict, Field

from .models import HASH_PATTERN
from .reference import split_uri, validate


@dataclass(frozen=True)
class Indexed:
    row_id: int


@dataclass(frozen=True)
class Existing:
    row_id: int


@dataclass(frozen=True)
class Deleted:
    row_id: int


@dataclass(frozen=True)
class Pinned:
    """A live cache entry names the wasm, so nothing changed."""


@dataclass(frozen=True)
class Won:
    row_id: int


@dataclass(frozen=True)
class Lost:
    """Another entry already holds the cache key, so nothing changed."""


@dataclass(frozen=True)
class WasmGone:
    """The wasm row is missing or soft deleted, so nothing changed."""


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_key: str = Field(pattern=HASH_PATTERN)
    wasm_uri: str = Field(min_length=1)
    componentize_py_version: str = Field(min_length=1)
    wit_version: str = Field(min_length=1)
    last_used_at: datetime | None = Field(
        default=None, description="The database clock when absent."
    )


def _one(conn: Any, sql: str, params: Any) -> tuple | None:
    with conn.cursor(row_factory=tuple_row) as cur:
        return cur.execute(sql, params).fetchone()


def index_reference(conn: Any, reference: Any) -> Indexed | Existing:
    """Stores a validated reference once. A uri that is already indexed is `Existing`."""
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
    return Existing(_one(conn, "SELECT row_id FROM artifact WHERE uri = %s", (ref.uri,))[0])


def mark_deleted(conn: Any, uri: str, at: datetime) -> Deleted | Pinned:
    """Soft deletes a row, keeping it, unless a live cache entry names it as its wasm.

    The row lock comes first and the guarded update is a statement of its own, because a single
    statement would not see a cache entry that committed while it waited for the lock.
    """
    if _one(conn, "SELECT 1 FROM artifact WHERE uri = %s FOR NO KEY UPDATE", (uri,)) is None:
        raise LookupError(f"no artifact row for {uri}")
    row = _one(
        conn,
        "UPDATE artifact SET deleted_at = COALESCE(deleted_at, %(at)s) WHERE uri = %(uri)s"
        " AND NOT EXISTS (SELECT 1 FROM artifact c WHERE c.kind = 'compile-cache'"
        " AND c.wasm_uri = %(uri)s AND c.deleted_at IS NULL) RETURNING row_id",
        {"uri": uri, "at": at},
    )
    return Deleted(row[0]) if row else Pinned()


def references_to(conn: Any, uri: str) -> list[str]:
    """The cache keys of the live cache entries that name the uri as their wasm."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(
            "SELECT cache_key FROM artifact WHERE wasm_uri = %s AND deleted_at IS NULL"
            " ORDER BY row_id",
            (uri,),
        )
        return [row[0] for row in cur.fetchall()]


def record_cache_entry(conn: Any, entry: Any) -> Won | Lost | WasmGone:
    """Writes a cache entry for a live wasm, the first writer of a cache key winning.

    The check of the wasm, its `FOR SHARE` lock and the insert are one statement, so a soft delete
    of the wasm that commits first is seen and one that comes later waits for this transaction.
    """
    e = entry if isinstance(entry, CacheEntry) else CacheEntry.model_validate(entry)
    live, row_id = _one(
        conn,
        "WITH wasm AS ("
        " SELECT row_id FROM artifact WHERE uri = %(wasm_uri)s AND deleted_at IS NULL FOR SHARE"
        "), won AS ("
        " INSERT INTO artifact (kind, cache_key, wasm_uri, componentize_py_version, wit_version,"
        " last_used_at)"
        " SELECT 'compile-cache', %(cache_key)s, %(wasm_uri)s, %(componentize_py_version)s,"
        " %(wit_version)s, COALESCE(%(last_used_at)s, now())"
        " WHERE EXISTS (SELECT 1 FROM wasm)"
        " ON CONFLICT (cache_key) WHERE kind = 'compile-cache' DO NOTHING RETURNING row_id"
        ") SELECT EXISTS (SELECT 1 FROM wasm), (SELECT row_id FROM won)",
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
