"""The lifecycle, days-arm and days-check modes: objects that a rule removes, and objects it keeps.

The rules are added to the rules a bucket already has, and a past-dated rule is taken out again
after the run, so a probe against a live store leaves its configuration as it found it.
"""

import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import ClientError

from .checks import NO_LIFECYCLE, Report, Store, missing
from .s3 import error_code

POLL_SECONDS = 5
# A rule of one day runs when the object is a day old and up to one detection interval later.
DAYS_DUE_HOURS = 25
PAST = datetime(2020, 1, 1, tzinfo=UTC)


def rules_of(s3, bucket: str) -> list[dict]:
    try:
        return s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
    except ClientError as error:
        if error_code(error) not in NO_LIFECYCLE:
            raise
        return []


def put_rules(s3, bucket: str, rules: list[dict]) -> None:
    if rules:
        s3.put_bucket_lifecycle_configuration(
            Bucket=bucket, LifecycleConfiguration={"Rules": rules}
        )
    else:
        s3.delete_bucket_lifecycle(Bucket=bucket)


@contextmanager
def rule_added(s3, bucket: str, rule: dict):
    """Adds one rule to a bucket and removes it again, whatever the other rules are."""
    before = rules_of(s3, bucket)
    put_rules(s3, bucket, [r for r in before if r["ID"] != rule["ID"]] + [rule])
    try:
        yield
    finally:
        put_rules(s3, bucket, [r for r in rules_of(s3, bucket) if r["ID"] != rule["ID"]])


def prefix_rule(rule_id: str, prefix: str, expiration: dict) -> dict:
    return {
        "ID": rule_id,
        "Status": "Enabled",
        "Filter": {"Prefix": prefix},
        "Expiration": expiration,
    }


def run_lifecycle(store: Store, report: Report, product, wait: float) -> None:
    """A past-dated rule on one prefix, one object under it and three controls outside it."""
    store.create_buckets()
    armed_bucket, other_bucket = store.buckets[0], store.buckets[1]
    armed = f"{store.prefix}armed/"
    keys = {
        "armed": (armed_bucket, f"{armed}object"),
        "same bucket, other prefix": (armed_bucket, f"{store.prefix}control/object"),
        "other bucket, same prefix": (other_bucket, f"{armed}object"),
        "other bucket, other prefix": (other_bucket, f"{store.prefix}control/object"),
    }
    rule = prefix_rule("storecheck-date", armed, {"Date": PAST})
    try:
        for bucket, key in keys.values():
            store.admin.put_object(Bucket=bucket, Key=key, Body=b"expiry")
        with rule_added(store.admin, armed_bucket, rule):
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and store.exists(*keys["armed"]):
                time.sleep(POLL_SECONDS)
        gone = not store.exists(*keys["armed"])
        kept = sum(store.exists(*key) for name, key in keys.items() if name != "armed")
        detail = f"expired: {int(gone)} of 1, controls kept: {kept} of 3"
        report.line("ok" if gone and kept == 3 else "FAIL", "lifecycle", detail)
    finally:
        store.sweep()


def run_days_arm(store: Store, report: Report, product, state: Path) -> None:
    """A rule of one day on one prefix, one object under it and one outside, recorded in `state`."""
    store.create_buckets()
    bucket = store.buckets[0]
    prefix = f"{store.prefix}days/"
    rule = prefix_rule(f"storecheck-days-{store.prefix.split('/')[1]}", prefix, {"Days": 1})
    store.admin.put_object(Bucket=bucket, Key=f"{prefix}object", Body=b"days")
    store.admin.put_object(Bucket=bucket, Key=f"{store.prefix}control", Body=b"days")
    put_rules(store.admin, bucket, rules_of(store.admin, bucket) + [rule])
    due = time.time() + DAYS_DUE_HOURS * 3600
    document = {
        "bucket": bucket,
        "rule": rule["ID"],
        "armed": f"{prefix}object",
        "control": f"{store.prefix}control",
        "due": due,
    }
    state.write_text(json.dumps(document, indent=2) + "\n")
    stamp = datetime.fromtimestamp(due, UTC).isoformat(timespec="seconds")
    report.line("ok", "days-arm", f"check after {stamp} with days-check --state {state}")


def run_days_check(store: Store, report: Report, product, state: Path) -> None:
    """Refuses before the recorded time, then checks the object is gone and the control is kept."""
    try:
        document = json.loads(state.read_text())
    except (OSError, ValueError) as error:
        report.line("FAIL", "days-check", f"{state}: no armed state ({error})")
        return
    if time.time() < document["due"]:
        stamp = datetime.fromtimestamp(document["due"], UTC).isoformat(timespec="seconds")
        report.line("FAIL", "days-check", f"not yet due, check after {stamp}")
        return
    s3, bucket = store.admin, document["bucket"]
    gone = missing(lambda: s3.head_object(Bucket=bucket, Key=document["armed"]))
    kept = store.exists(bucket, document["control"])
    report.line("ok" if gone else "FAIL", "days-check expired", "gone" if gone else "still there")
    report.line("ok" if kept else "FAIL", "days-check control", "kept" if kept else "removed")
    if gone and kept:
        s3.delete_object(Bucket=bucket, Key=document["control"])
        put_rules(s3, bucket, [r for r in rules_of(s3, bucket) if r["ID"] != document["rule"]])
