"""The compile cache client over the artifact index.

A lookup is a miss only when the index has no live entry or the wasm it names is missing or has
another size than recorded. A fault of the database or the store is raised and is never a miss, so
an outage cannot queue a build for every job. The hash of the wasm is checked where it is used.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from wdf_artifactindex import (
    CacheEntry,
    Invalidated,
    Lost,
    NoEntry,
    RowPurged,
    WasmGone,
    Won,
    invalidate_cache_entry,
    link_use,
    lookup_cache_entry,
    record_cache_entry,
    touch_cache_entry,
)
from wdf_events.models import ContentReference

from .client import as_reference, wall_now
from .errors import ArtifactNotFound

STUBBED = ()

__all__ = [
    "Hit",
    "Lost",
    "Miss",
    "WasmGone",
    "Won",
    "invalidate",
    "lookup",
    "record",
    "touch",
    "use",
]


@dataclass(frozen=True)
class Hit:
    """A live entry whose wasm exists in the store, with the references the consumer reads."""

    key: str
    wasm: ContentReference
    build_info: ContentReference | None
    entry: CacheEntry


@dataclass(frozen=True)
class Miss:
    pass


def _reference(conn: Any, uri: str) -> ContentReference | None:
    row = conn.execute(
        "SELECT kind, uri, sha256, size, name FROM artifact WHERE uri = %s AND deleted_at IS NULL",
        (uri,),
    ).fetchone()
    if row is None:
        return None
    return as_reference(dict(zip(("kind", "uri", "sha256", "size", "name"), row, strict=True)))


def lookup(conn: Any, client: Any, key: str) -> Hit | Miss:
    """The live entry of the key when its wasm exists with the recorded size, otherwise a miss."""
    entry = lookup_cache_entry(conn, key)
    if entry is None:
        return Miss()
    wasm = _reference(conn, entry.wasm_uri)
    if wasm is None:
        return Miss()
    try:
        stored = client.head(wasm)
    except ArtifactNotFound:
        return Miss()
    if stored.size != wasm.size:
        return Miss()
    info = entry.build_info_uri and _reference(conn, entry.build_info_uri)
    return Hit(key, wasm, info or None, entry)


def use(conn: Any, hit: Hit, job_id: str) -> None:
    """Links the wasm and the build-info of a hit to the job and renews the entry's last use.

    The links are written in one transaction, so a job never holds the wasm without its
    build-info. A purged row cannot happen while the live entry names it, so it is reported as a
    missing object.
    """
    at = wall_now()
    with conn.transaction():
        for reference in (hit.wasm, hit.build_info):
            if reference is not None and isinstance(
                link_use(conn, job_id, reference.uri, at), RowPurged
            ):
                raise ArtifactNotFound(f"{reference.uri} was purged under a live cache entry")
        touch_cache_entry(conn, hit.key, at)


def record(
    conn: Any, wasm: Any, build_info: Any, key: str, versions: Mapping[str, str]
) -> Won | Lost | WasmGone:
    """Writes the entry of a compile whose wasm and build-info were committed, first writer wins.

    The versions are the `componentize_py_version` and `wit_version` that the key was derived from.
    """
    entry = CacheEntry(
        cache_key=key,
        wasm_uri=as_reference(wasm).uri,
        build_info_uri=as_reference(build_info).uri,
        componentize_py_version=versions["componentize_py_version"],
        wit_version=versions["wit_version"],
    )
    return record_cache_entry(conn, entry)


def invalidate(conn: Any, key: str, at: datetime) -> Invalidated | NoEntry:
    """Retires the live entry of the key, which a lost or corrupt cached wasm calls for."""
    return invalidate_cache_entry(conn, key, at)


def touch(conn: Any, key: str, at: datetime) -> None:
    """Moves the last use of the entry forward, never back."""
    touch_cache_entry(conn, key, at)
