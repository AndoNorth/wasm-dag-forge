"""One idempotent reaper run: results and checkpoints of terminal jobs, the prune of running jobs,
the expiry of compile cache entries, and the collection of content-addressed blobs with the
sweep of their orphans.

Every comparison of time uses the `now` argument and never a clock of the database or the host,
so a repeated run with the same `now` finds nothing more to do.
A run takes a session advisory lock, so two runs never overlap and the later one does nothing.
"""

from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml
from wdf_artifactclient.checkpoint import MIN_KEEP, prune_plan
from wdf_artifactclient.contract import check_retention, safety_net_days
from wdf_artifactclient.models import CONTRACT, Retention
from wdf_artifactindex import checkpoints_of, expired_cache_entries

from .content import OBJECT_BATCH, Hooks, collect_content, sweep_orphans

STUBBED = ()

LOCK_NAME = "wdf-artifact-reaper"
JOB_PAGE = 100
CACHE_PAGE = 500
# The statuses whose checkpoints a prune trims. A paused, admitted or resuming job is never touched.
PRUNED_STATUSES = ("running",)
FIRST_JOB = ("0001-01-01T00:00:00+00:00", "00000000-0000-0000-0000-000000000000")


class ReapFailed(Exception):
    """The store refused to remove objects that a pass selected."""


@dataclass
class ReapReport:
    """The counters of a run: what was handled, kept, pruned and expired.

    `ran` is false when another run held the lock, and every counter is then zero.
    `tombstoned` and `purged` count the rows of the collector, and `orphans` the objects that the
    sweep deleted.
    """

    results_jobs: int = 0
    checkpoint_jobs: int = 0
    deleted_objects: int = 0
    kept_live: int = 0
    pruned_checkpoints: int = 0
    expired_cache_entries: int = 0
    tombstoned: int = 0
    purged: int = 0
    orphans: int = 0
    ran: bool = True

    def counters(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "ran"}


@dataclass(frozen=True)
class Rule:
    """What one pass over the terminal jobs of a bucket deletes, and how it records being done."""

    bucket: str
    window: str
    done_column: str
    counter: str
    prefixes: Callable[[str, str, str], tuple[str, ...]]


RESULTS = Rule(
    "wdf-results",
    "results_days",
    "results_done_at",
    "results_jobs",
    lambda tenant, batch, job: (f"{tenant}/{batch}/{job}/", f"{tenant}/{batch}/aggregate/{job}/"),
)
CHECKPOINTS = Rule(
    "wdf-checkpoints",
    "checkpoints_days",
    "checkpoints_done_at",
    "checkpoint_jobs",
    lambda tenant, batch, job: (f"{tenant}/{batch}/{job}/",),
)


def load_windows() -> Retention:
    """The windows of the artifact contract, read through package data and checked against the
    safety net of the store."""
    windows = Retention.model_validate(yaml.safe_load((CONTRACT / "retention.yaml").read_text()))
    check_retention(windows)
    return windows


def remove_prefix(
    s3: Any, bucket: str, prefix: str, heartbeat: Callable[[], None], dry_run: bool
) -> int:
    """Deletes every object under the prefix, a batch at a time, and returns how many.

    A heartbeat follows each batch. The listing starts over after a delete and ends on an empty
    page, so a clean return means the prefix is empty. A dry run only counts.
    """
    count, token, previous = 0, None, None
    while True:
        extra = {"ContinuationToken": token} if token else {}
        page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=OBJECT_BATCH, **extra)
        keys = [item["Key"] for item in page.get("Contents", [])]
        if keys and not dry_run:
            if keys == previous:
                raise ReapFailed(f"{bucket}/{keys[0]} is still listed after a delete")
            reply = s3.delete_objects(
                Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True}
            )
            if reply.get("Errors"):
                raise ReapFailed(f"{bucket}: {reply['Errors'][0]}")
        previous = keys
        count += len(keys)
        if keys:
            heartbeat()
        if dry_run:
            token = page.get("NextContinuationToken")
            if not token:
                return count
        elif not keys:
            return count


def reap_terminal(
    conn: Any,
    s3: Any,
    now: datetime,
    windows: Retention,
    rule: Rule,
    report: ReapReport,
    heartbeat: Callable[[], None],
    dry_run: bool,
) -> None:
    """Removes the objects of the terminal jobs whose window ended and that are not marked done.

    A job beyond the hard expiry of the bucket is left to the lifecycle rule of the store. The rows
    are marked first and the marker comes last, so a run that stopped between them is repeated.
    """
    statuses = sorted(windows.statuses)
    cutoffs = [now - timedelta(days=getattr(windows.statuses[s], rule.window)) for s in statuses]
    floor = now - timedelta(days=safety_net_days()[rule.bucket])
    after = FIRST_JOB
    while True:
        due = conn.execute(
            "SELECT j.job_id::text, j.tenant, j.batch_id::text, j.terminal_at FROM accepted_job j"
            " JOIN unnest(%(statuses)s::text[], %(cutoffs)s::timestamptz[]) AS w(status, cutoff)"
            " ON w.status = j.status LEFT JOIN reaper_job r ON r.job_id = j.job_id"
            " WHERE j.terminal_at <= w.cutoff AND j.terminal_at > %(floor)s"
            f" AND r.{rule.done_column} IS NULL"
            " AND (j.terminal_at, j.job_id) > (%(after_at)s::timestamptz, %(after_id)s::uuid)"
            " ORDER BY j.terminal_at, j.job_id LIMIT %(limit)s",
            {
                "statuses": statuses,
                "cutoffs": cutoffs,
                "floor": floor,
                "after_at": after[0],
                "after_id": after[1],
                "limit": JOB_PAGE,
            },
        ).fetchall()
        for job, tenant, batch, terminal_at in due:
            prefixes = rule.prefixes(tenant, batch, job)
            if not dry_run:
                with conn.transaction():
                    for prefix in prefixes:
                        mark_deleted_under(conn, rule.bucket, prefix, now)
            for prefix in prefixes:
                report.deleted_objects += remove_prefix(s3, rule.bucket, prefix, heartbeat, dry_run)
            if not dry_run:
                conn.execute(
                    f"INSERT INTO reaper_job (job_id, {rule.done_column}) VALUES (%(job)s, %(now)s)"
                    f" ON CONFLICT (job_id) DO UPDATE SET"
                    f" {rule.done_column} = EXCLUDED.{rule.done_column}",
                    {"job": job, "now": now},
                )
            setattr(report, rule.counter, getattr(report, rule.counter) + 1)
            after = (terminal_at.isoformat(), job)
        if len(due) < JOB_PAGE:
            return


def mark_deleted_under(conn: Any, bucket: str, prefix: str, now: datetime) -> None:
    conn.execute(
        "UPDATE artifact SET deleted_at = %s WHERE bucket = %s AND starts_with(key, %s)"
        " AND deleted_at IS NULL",
        (now, bucket, prefix),
    )


def prune_running(
    conn: Any,
    s3: Any,
    now: datetime,
    windows: Retention,
    report: ReapReport,
    heartbeat: Callable[[], None],
    dry_run: bool,
) -> None:
    """Trims each task of a running job to its newest checkpoints, by the keep count.

    The objects go first and the row after, because a run that stopped between them finds the same
    surplus again, whereas a marked row with its objects left would never be selected.
    """
    keep = max(windows.checkpoint_keep, MIN_KEEP)
    tasks = conn.execute(
        "SELECT a.producer_job::text, a.producer_task FROM artifact a"
        " JOIN accepted_job j ON j.job_id = a.producer_job"
        " WHERE a.kind = 'checkpoint' AND a.deleted_at IS NULL AND j.status = ANY(%s)"
        " GROUP BY a.producer_job, a.producer_task HAVING count(*) > %s"
        " ORDER BY a.producer_job, a.producer_task",
        (list(PRUNED_STATUSES), keep),
    ).fetchall()
    for job, task in tasks:
        live = checkpoints_of(conn, job, task)
        doomed = prune_plan(live, keep)
        report.kept_live += len(live) - len(doomed)
        for row in doomed:
            folder = row.key.rpartition("/")[0] + "/"
            report.deleted_objects += remove_prefix(s3, row.bucket, folder, heartbeat, dry_run)
            if not dry_run:
                with conn.transaction():
                    mark_deleted_under(conn, row.bucket, folder, now)
            report.pruned_checkpoints += 1


def expire_cache(
    conn: Any,
    now: datetime,
    windows: Retention,
    report: ReapReport,
    heartbeat: Callable[[], None],
    dry_run: bool,
) -> None:
    """Retires the compile cache entries that nothing used for the cache window."""
    before = now - timedelta(days=windows.cache_unused_days)
    if dry_run:
        report.expired_cache_entries = conn.execute(
            "SELECT count(*) FROM artifact WHERE kind = 'compile-cache' AND deleted_at IS NULL"
            " AND last_used_at < %s",
            (before,),
        ).fetchone()[0]
        return
    while True:
        with conn.transaction():
            keys = expired_cache_entries(conn, before, now, CACHE_PAGE)
        if not keys:
            return
        report.expired_cache_entries += len(keys)
        heartbeat()


def reap_once(
    conn: Any,
    client: Any,
    now: datetime,
    windows: Retention,
    *,
    heartbeat: Callable[[], None] | None = None,
    dry_run: bool = False,
    hooks: Hooks | None = None,
) -> ReapReport:
    """Runs the passes once under the reaper's database login and store credentials.

    `conn` is an autocommit connection of the `wdf_reaper` role and `client` is a boto3 client of
    the `reaper` store role. `heartbeat` is called after each batch of objects. A dry run lists but
    changes no row and no object. `hooks` names callables for the points of `content.HOOK_POINTS`.
    """
    if not conn.autocommit:
        raise ValueError("the reaper needs an autocommit connection")
    if now.tzinfo is None:
        raise ValueError("now needs a time zone")
    beat = heartbeat or (lambda: None)
    now = now.astimezone(UTC)
    (held,) = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,)).fetchone()
    if not held:
        return ReapReport(ran=False)
    report = ReapReport()
    try:
        reap_terminal(conn, client, now, windows, RESULTS, report, beat, dry_run)
        reap_terminal(conn, client, now, windows, CHECKPOINTS, report, beat, dry_run)
        prune_running(conn, client, now, windows, report, beat, dry_run)
        expire_cache(conn, now, windows, report, beat, dry_run)
        report.tombstoned, report.purged = collect_content(
            conn, client, now, windows, beat, dry_run, hooks
        )
        report.orphans = sweep_orphans(conn, client, now, windows, beat, dry_run, hooks)
    finally:
        conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
    return report
