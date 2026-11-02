"""The disk-full, recover, capacity and marker modes.

Disk-full and recover need a store on a size-capped volume that nothing else writes to.
Recover runs a command inside the store to free space, which `--exec` gives as a prefix
such as `docker exec -i ID` or `kubectl exec -i POD -c store --`.
"""

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass, field

from botocore.exceptions import ClientError

from .checks import MIB, Report, Store, contract_gaps, read, sha
from .s3 import ALLOWED, error_code, status

OBJECT = 4 * MIB
# More objects than a capped volume holds, so a store with no cap fails and never fills a disk.
MAX_OBJECTS = 1024
MARKER_BUCKET_INDEX = 0
MARKER_KEY = "storecheck-marker/object"
VACUUM = "lock\nvolume.vacuum\nunlock\n"
SEAWEEDFS_SHELL = ["weed", "shell", "-master=127.0.0.1:9333"]
SETTLE_SECONDS = 60
DELETE_ATTEMPTS = 6
# The volume servers of SeaweedFS report their free space to the master every few seconds.
HEARTBEAT_SECONDS = 6
DELETE_PAUSE = 5
GAP = "delete-while-full"


def marker_body() -> bytes:
    """The same bytes every run, so the check after a restart needs no state file."""
    return hashlib.shake_256(b"wdf-storecheck-marker").digest(MIB)


def run_marker_put(store: Store, report: Report, product) -> None:
    store.create_buckets()
    store.admin.put_object(
        Bucket=store.buckets[MARKER_BUCKET_INDEX], Key=MARKER_KEY, Body=marker_body()
    )
    report.line("ok", "marker-put", f"{store.buckets[MARKER_BUCKET_INDEX]}/{MARKER_KEY}")


def run_marker_get(store: Store, report: Report, product) -> None:
    bucket = store.buckets[MARKER_BUCKET_INDEX]
    try:
        found = read(store.admin, bucket, MARKER_KEY)
    except ClientError as error:
        report.line("FAIL", "marker-get", f"{error_code(error)} {status(error)}")
        return
    intact = sha(found) == sha(marker_body())
    report.line("ok" if intact else "FAIL", "marker-get", "intact" if intact else "bytes differ")


def run_capacity(store: Store, report: Report, product) -> None:
    """One `bytes <bucket> <n>` line per bucket, summed from the listing of the reaper identity."""
    if "reaper" not in store.credentials:
        report.line("FAIL", "capacity", "the credentials file has no reaper identity")
        return
    s3 = store.as_role("reaper")
    for bucket in store.buckets:
        total = 0
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            total += sum(item["Size"] for item in page.get("Contents", []))
        report.say(f"bytes {bucket} {total}")


@dataclass
class FullDisk:
    """What happened to one capped store, from the first refused put to the put after recovery."""

    written: dict[str, bytes] = field(default_factory=dict, repr=False)
    refusal: tuple[str, int] = ("", 0)
    second_refusal: tuple[str, int] = ("", 0)
    survivors_exact: int = 0
    delete: str = ""
    put_after_delete: str = ""
    put_after_recovery: str = ""


def refused_put(s3, bucket: str, key: str, body: bytes) -> tuple[str, int] | None:
    """The error of a put that fails, or None when it succeeds."""
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body)
    except ClientError as error:
        return error_code(error), status(error)
    return None


def fill(store: Store, bucket: str, run: FullDisk) -> None:
    for n in range(MAX_OBJECTS):
        key, body = f"{store.prefix}fill/{n:04d}", os.urandom(OBJECT)
        failed = refused_put(store.admin, bucket, key, body)
        if failed:
            run.refusal = failed
            return
        run.written[key] = body
    raise AssertionError(f"{MAX_OBJECTS} objects of 4 MiB were written and the store is not full")


def batch_delete(store: Store, bucket: str) -> str:
    """The outcome of deleting every object of the fill with the reaper identity in one request.

    A store that has only just filled can answer `ServiceUnavailable` and ask to retry.
    """
    reaper = store.as_role("reaper")
    prefix = f"{store.prefix}fill/"
    outcome = ""
    for attempt in range(DELETE_ATTEMPTS):
        keys = [
            item["Key"]
            for item in reaper.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
        ]
        if not keys:
            return ALLOWED
        try:
            reply = reaper.delete_objects(
                Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys]}
            )
        except ClientError as error:
            outcome = f"error:{error_code(error) or status(error)}"
        else:
            errors = reply.get("Errors")
            if not errors:
                return ALLOWED
            outcome = f"error:{len(errors)} of {len(keys)} ({errors[0]['Code']})"
        time.sleep(DELETE_PAUSE)
    return outcome


def settle(probe) -> str:
    """The outcome of a put that may need a moment after recovery before the store takes it."""
    deadline = time.monotonic() + SETTLE_SECONDS
    while probe():
        if time.monotonic() >= deadline:
            return "refused"
        time.sleep(2)
    return ALLOWED


def observe_full_disk(store: Store, product: str | None, recover_with=None) -> FullDisk:
    """Fills the volume, then checks refusals, survivors and delete, and recovers when asked to."""
    store.create_buckets()
    bucket = store.buckets[0]
    run = FullDisk()
    try:
        time.sleep(HEARTBEAT_SECONDS)
        fill(store, bucket, run)
        again = refused_put(store.admin, bucket, f"{store.prefix}fill/again", os.urandom(OBJECT))
        run.second_refusal = again or ("", 0)
        run.survivors_exact = sum(
            sha(read(store.admin, bucket, key)) == sha(body) for key, body in run.written.items()
        )
        run.delete = batch_delete(store, bucket)
        # An object of the size that filled the volume, since a few bytes can still fit.
        probe = lambda: refused_put(  # noqa: E731
            store.admin, bucket, f"{store.prefix}probe", os.urandom(OBJECT)
        )
        run.put_after_delete = "refused" if probe() else ALLOWED
        if recover_with:
            recover_with(store)
            run.put_after_recovery = settle(probe)
    finally:
        store.sweep()
    return run


def run_disk_full(store: Store, report: Report, product, recover_with=None) -> None:
    run = observe_full_disk(store, product, recover_with)
    n = len(run.written)
    report.line(
        "ok" if run.refusal[1] == 500 else "FAIL",
        "disk-full refusal",
        f"after {n} objects of 4 MiB: {run.refusal[0]} {run.refusal[1]}",
    )
    report.line(
        "ok" if run.second_refusal[1] == 500 else "FAIL",
        "disk-full stays-refused",
        f"{run.second_refusal[0]} {run.second_refusal[1]}",
    )
    report.line(
        "ok" if run.survivors_exact == n else "FAIL",
        "disk-full survivors",
        f"{run.survivors_exact} of {n} byte exact",
    )
    listed = "reaper" in contract_gaps(product).get(GAP, set())
    deleted = run.delete == ALLOWED
    if listed and not deleted:
        report.line("known-gap", GAP, "a full volume cannot be emptied through S3, as recorded")
    elif listed:
        report.line(
            "FAIL",
            f"disk-full {GAP}",
            "the recorded gap is closed, so the contract no longer lists it",
        )
    else:
        report.line(
            "ok" if deleted else "FAIL",
            f"disk-full {GAP}",
            "batch delete works" if deleted else run.delete,
        )
    return run


def vacuum_through(exec_prefix: list[str]):
    """The recovery of SeaweedFS: a vacuum through `weed shell` inside the store container."""

    def recover(store: Store) -> None:
        subprocess.run(
            [*exec_prefix, *SEAWEEDFS_SHELL],
            input=VACUUM,
            text=True,
            check=True,
            capture_output=True,
        )

    return recover


def free_outside_s3(exec_prefix: list[str], keys=2):
    """The recovery of RustFS: remove the first fill objects from its data directory."""

    def recover(store: Store) -> None:
        paths = [f"/data/{store.buckets[0]}/{store.prefix}fill/{n:04d}" for n in range(keys)]
        subprocess.run([*exec_prefix, "rm", "-rf", *paths], check=True, capture_output=True)

    return recover


def run_recover(store: Store, report: Report, product, exec_prefix: list[str]) -> None:
    recover_with = (
        vacuum_through(exec_prefix) if product != "rustfs" else free_outside_s3(exec_prefix)
    )
    run = run_disk_full(store, report, product, recover_with)
    needed = run.put_after_delete == "refused"
    report.line(
        "ok" if needed else "FAIL",
        "recover needed",
        "a put is still refused after the delete" if needed else "a put works with no recovery",
    )
    report.line(
        "ok" if run.put_after_recovery == ALLOWED else "FAIL", "recover put", run.put_after_recovery
    )
