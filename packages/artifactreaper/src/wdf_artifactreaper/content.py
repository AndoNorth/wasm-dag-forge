"""Collection of content-addressed blobs, in two passes.

The collector tombstones a live row that nothing holds and, in a later run, deletes the object of a
row that is still tombstoned past the interval and still unreferenced. Both decisions are taken by
the index library under the row lock, and the object is removed before that transaction ends, so a
link or a revive that waits for the lock sees the outcome.

The orphan sweep lists the content-addressed buckets from a stored cursor and deletes the objects
that have no live row once they are older than the orphan grace.

Every comparison of time uses the `now` argument. A hook is called with the uri at the named point,
so a test can interleave another caller there.
"""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from wdf_artifactclient.keys import split_uri
from wdf_artifactclient.models import Retention
from wdf_artifactindex import Deleted, Purged, purge, tombstone, unreferenced_content

# Objects per listing and per heartbeat. An estimate.
OBJECT_BATCH = 500
# Listing pages one run reads from a bucket before it leaves the rest to the next run.
SWEEP_PAGES = 20
CONTENT_BUCKETS = ("wdf-sources", "wdf-components")

BEFORE_TOMBSTONE = "tombstone:before"
BEFORE_PURGE = "purge:before"
PURGE_LOCKED = "purge:locked"
SWEEP_BEFORE_DELETE = "sweep:before-delete"
SWEEP_BEFORE_LOCK = "sweep:before-lock"
SWEEP_LOCKED = "sweep:locked"
HOOK_POINTS = frozenset(
    {
        BEFORE_TOMBSTONE,
        BEFORE_PURGE,
        PURGE_LOCKED,
        SWEEP_BEFORE_DELETE,
        SWEEP_BEFORE_LOCK,
        SWEEP_LOCKED,
    }
)

Hooks = Mapping[str, Callable[[str], None]]


def checked(hooks: Hooks | None) -> Hooks:
    unknown = set(hooks or ()) - HOOK_POINTS
    if unknown:
        raise ValueError(f"unknown hook points: {sorted(unknown)}")
    return hooks or {}


def fire(hooks: Hooks, point: str, uri: str) -> None:
    if point in hooks:
        hooks[point](uri)


def delete_object(s3: Any, uri: str) -> None:
    bucket, key = split_uri(uri)
    s3.delete_object(Bucket=bucket, Key=key)


def collect_content(
    conn: Any,
    s3: Any,
    now: datetime,
    windows: Retention,
    heartbeat: Callable[[], None],
    dry_run: bool,
    hooks: Hooks | None = None,
) -> tuple[int, int]:
    """Tombstones the unreferenced rows and purges the ones tombstoned for the interval.

    Returns how many rows were tombstoned and how many objects were purged. A row tombstoned by
    this call is never purged by it, because the interval has not passed. A dry run counts.
    """
    hooks = checked(hooks)
    rules = windows.content
    grace = timedelta(days=rules.unreferenced_grace_days)
    interval = timedelta(hours=rules.tombstone_interval_hours)
    # ponytail: the candidates are held in memory, a page of rows each if the store grows past it
    candidates = unreferenced_content(conn, now, grace)
    tombstoned = purged = handled = 0
    for row in candidates:
        if row.deleted_at is None:
            if dry_run:
                tombstoned += 1
            else:
                fire(hooks, BEFORE_TOMBSTONE, row.uri)
                with conn.transaction():
                    tombstoned += isinstance(tombstone(conn, row.uri, now, grace), Deleted)
        elif row.deleted_at <= now - interval:
            if dry_run:
                purged += 1
            else:
                fire(hooks, BEFORE_PURGE, row.uri)
                purged += purge_object(conn, s3, row.uri, now, interval, grace, hooks)
        else:
            continue
        handled += 1
        if handled % OBJECT_BATCH == 0:
            heartbeat()
    return tombstoned, purged


def purge_object(
    conn: Any,
    s3: Any,
    uri: str,
    now: datetime,
    interval: timedelta,
    grace: timedelta,
    hooks: Hooks,
) -> int:
    """Records the purge under the row lock and removes the object before the lock is released.

    A failed delete rolls the purge back, so the row stays tombstoned and the next run retries.
    """
    with conn.transaction():
        if not isinstance(purge(conn, uri, now, interval, grace), Purged):
            return 0
        fire(hooks, PURGE_LOCKED, uri)
        delete_object(s3, uri)
    return 1


def read_cursor(conn: Any, bucket: str) -> str:
    row = conn.execute("SELECT position FROM reaper_cursor WHERE name = %s", (cursor_name(bucket),))
    found = row.fetchone()
    return found[0] if found else ""


def write_cursor(conn: Any, bucket: str, position: str, now: datetime) -> None:
    conn.execute(
        "INSERT INTO reaper_cursor (name, position, updated_at) VALUES (%(name)s, %(position)s,"
        " %(now)s) ON CONFLICT (name) DO UPDATE SET position = EXCLUDED.position,"
        " updated_at = EXCLUDED.updated_at",
        {"name": cursor_name(bucket), "position": position, "now": now},
    )


def cursor_name(bucket: str) -> str:
    return f"orphan-sweep:{bucket}"


def sweep_orphans(
    conn: Any,
    s3: Any,
    now: datetime,
    windows: Retention,
    heartbeat: Callable[[], None],
    dry_run: bool,
    hooks: Hooks | None = None,
) -> int:
    """Deletes the objects of the content-addressed buckets that have no live row and are old.

    A run reads at most `SWEEP_PAGES` pages of a bucket from its cursor, stores where it stopped
    and starts from the beginning after the last page, so a pass stays bounded on a large bucket.
    A dry run counts and leaves the cursor alone. Returns how many objects were deleted.
    """
    hooks = checked(hooks)
    rules = windows.content
    oldest = now - timedelta(days=rules.orphan_grace_days)
    deleted = 0
    for bucket in CONTENT_BUCKETS:
        position = read_cursor(conn, bucket)
        for _ in range(SWEEP_PAGES):
            extra = {"StartAfter": position} if position else {}
            page = s3.list_objects_v2(Bucket=bucket, MaxKeys=OBJECT_BATCH, **extra)
            listed = page.get("Contents", [])
            old = [item["Key"] for item in listed if item["LastModified"] <= oldest]
            deleted += sweep_page(conn, s3, bucket, old, now, windows, dry_run, hooks)
            heartbeat()
            more = page.get("IsTruncated", False) and bool(listed)
            position = listed[-1]["Key"] if more else ""
            if not dry_run:
                write_cursor(conn, bucket, position, now)
            if not more:
                break
    return deleted


def sweep_page(
    conn: Any,
    s3: Any,
    bucket: str,
    keys: list[str],
    now: datetime,
    windows: Retention,
    dry_run: bool,
    hooks: Hooks,
) -> int:
    """Deletes the orphans among the old objects of one listing page."""
    if not keys:
        return 0
    uris = [f"s3://{bucket}/{key}" for key in keys]
    rows = {
        uri: (deleted_at, purged_at)
        for uri, deleted_at, purged_at in conn.execute(
            "SELECT uri, deleted_at, purged_at FROM artifact WHERE uri = ANY(%s)", (uris,)
        ).fetchall()
    }
    grace = timedelta(days=windows.content.unreferenced_grace_days)
    interval = timedelta(hours=windows.content.tombstone_interval_hours)
    deleted = 0
    for uri in uris:
        if uri not in rows:
            if not dry_run:
                fire(hooks, SWEEP_BEFORE_DELETE, uri)
                delete_object(s3, uri)
            deleted += 1
            continue
        deleted_at, purged_at = rows[uri]
        if deleted_at is None:
            continue
        if dry_run:
            if purged_at is not None:
                deleted += 1
            else:
                held = unreferenced_content(conn, now, grace, uri)
                deleted += bool(held) and held[0].deleted_at <= now - interval
            continue
        fire(hooks, SWEEP_BEFORE_LOCK, uri)
        deleted += sweep_row(conn, s3, uri, now, interval, grace, hooks)
    return deleted


def sweep_row(
    conn: Any,
    s3: Any,
    uri: str,
    now: datetime,
    interval: timedelta,
    grace: timedelta,
    hooks: Hooks,
) -> int:
    """Deletes the object of a tombstoned or purged row under the row lock, 1 when it did.

    A tombstoned row goes through the purge, so the interval and the reference rule of the
    collector apply. A purged row whose object exists again is deleted unless a revive got there.
    """
    with conn.transaction():
        found = conn.execute(
            "SELECT deleted_at, purged_at FROM artifact WHERE uri = %s FOR NO KEY UPDATE", (uri,)
        ).fetchone()
        if found is None or found[0] is None:
            return 0
        if found[1] is None and not isinstance(purge(conn, uri, now, interval, grace), Purged):
            return 0
        fire(hooks, SWEEP_LOCKED, uri)
        delete_object(s3, uri)
    return 1
