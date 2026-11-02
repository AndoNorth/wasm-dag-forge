"""Provisions the object store with the admin identity.

It sets the buckets, their lifecycle rules and the detection interval of the lifecycle job.
Usage: bootstrap.py apply|check ENDPOINT ADMIN_URL CREDENTIALS
`apply` is idempotent, and `check` exits 1 when the store differs from the contract.
"""

import json
import sys
import urllib.request

import yaml
from botocore.exceptions import ClientError
from wdf_storecheck.checks import NO_LIFECYCLE
from wdf_storecheck.contract import buckets, windows
from wdf_storecheck.models import CONTRACT, Lifecycle
from wdf_storecheck.provision import ADMIN
from wdf_storecheck.s3 import client, error_code, load_credentials

JOB_CONFIG = "/api/plugin/job-types/s3_lifecycle/config"


def days(window: str) -> int:
    block, target, name = window.split(".")
    return windows()[block][target][name]


def rules_of(bucket: str, contract: Lifecycle) -> list[dict]:
    """The S3 rules of a bucket, each over the whole bucket and named by its kind."""
    rules = []
    for rule in contract.buckets[bucket].rules:
        entry = {"ID": rule.kind, "Status": "Enabled", "Filter": {"Prefix": ""}}
        if rule.kind == "expire":
            entry["Expiration"] = {"Days": days(rule.window)}
        else:
            entry["AbortIncompleteMultipartUpload"] = {"DaysAfterInitiation": days(rule.window)}
        rules.append(entry)
    return sorted(rules, key=lambda r: r["ID"])


def current(s3, bucket: str) -> list[dict] | None:
    try:
        rules = s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
    except ClientError as error:
        if error_code(error) in NO_LIFECYCLE:
            return None
        raise
    return sorted(rules, key=lambda r: r["ID"])


def job_config(admin_url: str, minutes: int | None = None) -> dict:
    """Reads the lifecycle job configuration of the admin server, or sets its detection interval."""
    request = urllib.request.Request(admin_url + JOB_CONFIG)
    if minutes is not None:
        runtime = {
            "enabled": True,
            "detection_interval_minutes": minutes,
            "detection_timeout_seconds": 60,
            "max_jobs_per_detection": 1,
        }
        request = urllib.request.Request(
            admin_url + JOB_CONFIG,
            data=json.dumps({"admin_runtime": runtime}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
    with urllib.request.urlopen(request, timeout=30) as reply:
        return json.load(reply)["admin_runtime"]


def main(mode: str, endpoint: str, admin_url: str, credentials: str) -> int:
    contract = Lifecycle.model_validate(yaml.safe_load((CONTRACT / "lifecycle.yaml").read_text()))
    s3 = client(endpoint, load_credentials(credentials)[ADMIN])
    interval = contract.detection_interval_minutes
    if mode == "apply":
        for bucket in buckets():
            try:
                s3.create_bucket(Bucket=bucket)
            except ClientError as error:
                if error_code(error) not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
            s3.put_bucket_lifecycle_configuration(
                Bucket=bucket, LifecycleConfiguration={"Rules": rules_of(bucket, contract)}
            )
            print(f"bucket {bucket}: {len(rules_of(bucket, contract))} rules")
        job_config(admin_url, interval)
        print(f"lifecycle job: detection every {interval} minutes")
        return 0
    wrong = [b for b in buckets() if current(s3, b) != rules_of(b, contract)]
    if job_config(admin_url)["detection_interval_minutes"] != interval:
        wrong.append("detection interval")
    for name in wrong:
        print(f"differs: {name}", file=sys.stderr)
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
