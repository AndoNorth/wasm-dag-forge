"""The base and access modes of the S3 contract script.

Every function takes a `Store`, which bundles one endpoint with the credential of each role.
Each check reports through `Report`, one line per result, and a check that raises is a failure.
"""

import hashlib
import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass

import yaml
from botocore.exceptions import ClientError

from .models import CONTRACT, Products
from .provision import ADMIN
from .s3 import ALLOWED, REFUSED, Creds, client, error_code, outcome, status

MIB = 1024 * 1024
PART = 5 * MIB
OBJECT_ACTIONS = ("put", "get", "list", "multipart", "delete")
NO_LIFECYCLE = ("NoSuchLifecycleConfiguration", "NoSuchBucketLifecycleConfiguration")
PROBE_RULE = {
    "ID": "storecheck-probe",
    "Status": "Enabled",
    "Filter": {"Prefix": "storecheck/"},
    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
}


def contract_roles() -> dict[str, dict[str, list[str]]]:
    return yaml.safe_load((CONTRACT / "roles.yaml").read_text())["roles"]


def contract_buckets() -> list[str]:
    return yaml.safe_load((CONTRACT / "layout.yaml").read_text())["buckets"]


def contract_products() -> dict:
    document = yaml.safe_load((CONTRACT / "products.yaml").read_text())
    return Products.model_validate(document).products


def contract_gaps(product: str | None) -> dict[str, set[str]]:
    """The roles that the contract records a known gap for, per gap, on one product."""
    if product is None:
        return {}
    return {g.gap: set(g.roles) for g in contract_products()[product].known_gaps}


@dataclass
class Report:
    say: callable = print
    failures: int = 0
    passed: int = 0

    def line(self, state: str, name: str, detail: str = "") -> None:
        self.failures += state == "FAIL"
        self.passed += state == "ok"
        self.say(f"{state} {name}" + (f": {detail}" if detail else ""))

    def check(self, name: str, fn) -> None:
        """Runs one check, where a raised exception of any kind is the failure."""
        try:
            fn()
        except Exception as error:
            self.line("FAIL", name, f"{type(error).__name__}: {error}")
        else:
            self.line("ok", name)


class Store:
    def __init__(self, endpoint: str, credentials: dict[str, Creds]):
        self.endpoint = endpoint
        self.credentials = credentials
        self.buckets = contract_buckets()
        self.prefix = f"storecheck/{secrets.token_hex(4)}/"
        self.admin = client(endpoint, credentials[ADMIN])

    def as_role(self, role: str):
        return client(self.endpoint, self.credentials[role])

    def create_buckets(self) -> None:
        for bucket in self.buckets:
            try:
                self.admin.create_bucket(Bucket=bucket)
            except ClientError as error:
                if error_code(error) not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
            self.admin.head_bucket(Bucket=bucket)

    def sweep(self) -> None:
        """Best effort removal of every object and upload of this run, which never fails the run."""
        for bucket in self.buckets:
            try:
                for page in self.admin.get_paginator("list_objects_v2").paginate(
                    Bucket=bucket, Prefix=self.prefix
                ):
                    for item in page.get("Contents", []):
                        self.admin.delete_object(Bucket=bucket, Key=item["Key"])
                for upload in self.admin.list_multipart_uploads(
                    Bucket=bucket, Prefix=self.prefix
                ).get("Uploads", []):
                    self.admin.abort_multipart_upload(
                        Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
                    )
            except ClientError:
                pass

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.admin.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if status(error) == 404:
                return False
            raise
        return True


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(s3, bucket: str, key: str, **kwargs) -> bytes:
    return s3.get_object(Bucket=bucket, Key=key, **kwargs)["Body"].read()


def missing(call) -> bool:
    """True when the call fails with a not-found status."""
    try:
        call()
    except ClientError as error:
        return status(error) == 404
    return False


@contextmanager
def lifecycle_restored(s3, bucket: str):
    """Puts the lifecycle rules of a bucket back, so a probe never changes a live store."""
    try:
        before = s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
    except ClientError as error:
        if error_code(error) not in NO_LIFECYCLE:
            raise
        before = None
    try:
        yield
    finally:
        if before is None:
            s3.delete_bucket_lifecycle(Bucket=bucket)
        else:
            s3.put_bucket_lifecycle_configuration(
                Bucket=bucket, LifecycleConfiguration={"Rules": before}
            )


def base_checks(store: Store) -> list:
    """The 14 base checks, each a (name, function) pair run with the admin credential."""
    s3, first = store.admin, store.buckets[0]
    key = lambda name: f"{store.prefix}{name}"  # noqa: E731

    def buckets_created():
        store.create_buckets()

    def buckets_listed():
        names = {b["Name"] for b in s3.list_buckets()["Buckets"]}
        assert set(store.buckets) <= names, f"missing {sorted(set(store.buckets) - names)}"

    def first_write_per_bucket():
        for bucket in store.buckets:
            s3.put_object(Bucket=bucket, Key=key("first"), Body=bucket.encode())
            assert read(s3, bucket, key("first")) == bucket.encode()

    def put_get_exact():
        body = os.urandom(MIB // 4)
        s3.put_object(Bucket=first, Key=key("exact"), Body=body)
        assert sha(read(s3, first, key("exact"))) == sha(body)

    def head_size():
        s3.put_object(Bucket=first, Key=key("head"), Body=b"x" * 1234)
        assert s3.head_object(Bucket=first, Key=key("head"))["ContentLength"] == 1234

    def range_get():
        body = bytes(range(256)) * 4
        s3.put_object(Bucket=first, Key=key("range"), Body=body)
        assert read(s3, first, key("range"), Range="bytes=10-19") == body[10:20]

    def overwrite():
        s3.put_object(Bucket=first, Key=key("over"), Body=b"one")
        s3.put_object(Bucket=first, Key=key("over"), Body=b"two")
        assert read(s3, first, key("over")) == b"two"

    def list_prefix():
        for name in ("a/1", "a/2", "b/1"):
            s3.put_object(Bucket=first, Key=key(f"list/{name}"), Body=b"1")
        found = s3.list_objects_v2(Bucket=first, Prefix=key("list/a/")).get("Contents", [])
        assert [o["Key"] for o in found] == [key("list/a/1"), key("list/a/2")]

    def list_pagination():
        for n in range(5):
            s3.put_object(Bucket=first, Key=key(f"page/{n}"), Body=b"1")
        keys, pages, token = [], 0, None
        while True:
            extra = {"ContinuationToken": token} if token else {}
            reply = s3.list_objects_v2(Bucket=first, Prefix=key("page/"), MaxKeys=2, **extra)
            keys += [o["Key"] for o in reply.get("Contents", [])]
            pages += 1
            token = reply.get("NextContinuationToken")
            if not reply["IsTruncated"]:
                break
        assert keys == [key(f"page/{n}") for n in range(5)] and pages == 3, f"{pages} pages"

    def multipart_roundtrip():
        parts = [os.urandom(PART), os.urandom(MIB)]
        upload = s3.create_multipart_upload(Bucket=first, Key=key("mpu"))["UploadId"]
        done = []
        for number, data in enumerate(parts, 1):
            etag = s3.upload_part(
                Bucket=first, Key=key("mpu"), UploadId=upload, PartNumber=number, Body=data
            )["ETag"]
            done.append({"ETag": etag, "PartNumber": number})
        s3.complete_multipart_upload(
            Bucket=first, Key=key("mpu"), UploadId=upload, MultipartUpload={"Parts": done}
        )
        assert sha(read(s3, first, key("mpu"))) == sha(b"".join(parts))

    def multipart_abort():
        upload = s3.create_multipart_upload(Bucket=first, Key=key("aborted"))["UploadId"]
        s3.upload_part(
            Bucket=first, Key=key("aborted"), UploadId=upload, PartNumber=1, Body=b"x" * PART
        )
        s3.abort_multipart_upload(Bucket=first, Key=key("aborted"), UploadId=upload)
        assert not store.exists(first, key("aborted")), "an aborted upload left an object"
        left = s3.list_multipart_uploads(Bucket=first, Prefix=key("aborted")).get("Uploads", [])
        assert not left, "an aborted upload is still listed"

    def delete_then_missing():
        s3.put_object(Bucket=first, Key=key("gone"), Body=b"1")
        s3.delete_object(Bucket=first, Key=key("gone"))
        assert missing(lambda: s3.get_object(Bucket=first, Key=key("gone")))
        assert not store.exists(first, key("gone"))

    def unicode_key():
        name = key("uni/é ü/a b+c=d.bin")
        s3.put_object(Bucket=first, Key=name, Body=b"1")
        assert read(s3, first, name) == b"1"
        listed = s3.list_objects_v2(Bucket=first, Prefix=key("uni/")).get("Contents", [])
        assert [o["Key"] for o in listed] == [name]

    def lifecycle_roundtrip():
        with lifecycle_restored(s3, first):
            s3.put_bucket_lifecycle_configuration(
                Bucket=first, LifecycleConfiguration={"Rules": [PROBE_RULE]}
            )
            rules = s3.get_bucket_lifecycle_configuration(Bucket=first)["Rules"]
            assert [r["ID"] for r in rules] == [PROBE_RULE["ID"]]
            assert rules[0]["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1

    return [
        (fn.__name__.replace("_", "-"), fn)
        for fn in (
            buckets_created,
            buckets_listed,
            first_write_per_bucket,
            put_get_exact,
            head_size,
            range_get,
            overwrite,
            list_prefix,
            list_pagination,
            multipart_roundtrip,
            multipart_abort,
            delete_then_missing,
            unicode_key,
            lifecycle_roundtrip,
        )
    ]


def run_base(store: Store, report: Report) -> None:
    checks = base_checks(store)
    try:
        for name, fn in checks:
            report.check(f"base {name}", fn)
    finally:
        store.sweep()
    report.say(f"{report.passed} of {len(checks)} base checks passed")


def observe(store: Store, role: str, bucket: str, action: str) -> str:
    """What one role gets for one object action on one bucket: allowed, refused or error:<code>."""
    s3 = store.as_role(role)
    key = f"{store.prefix}{role}/{action}"
    if action == "put":
        return outcome(lambda: s3.put_object(Bucket=bucket, Key=key, Body=b"1"))
    if action == "get":
        return outcome(
            lambda: s3.get_object(Bucket=bucket, Key=f"{store.prefix}seed")["Body"].read()
        )
    if action == "list":
        return outcome(lambda: s3.list_objects_v2(Bucket=bucket, Prefix=store.prefix, MaxKeys=1))
    if action == "delete":
        store.admin.put_object(Bucket=bucket, Key=key, Body=b"1")
        return outcome(lambda: s3.delete_object(Bucket=bucket, Key=key))
    created = {}

    def upload():
        created["id"] = s3.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
        part = s3.upload_part(
            Bucket=bucket, Key=key, UploadId=created["id"], PartNumber=1, Body=b"1"
        )
        s3.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=created["id"],
            MultipartUpload={"Parts": [{"ETag": part["ETag"], "PartNumber": 1}]},
        )

    result = outcome(upload)
    if result == REFUSED and created:
        result = "error:refused-after-create"
    return result


def observe_matrix(store: Store) -> tuple[dict[tuple[str, str, str], str], list[str]]:
    """Every role but admin against every bucket and object action, and what refused writes left."""
    for bucket in store.buckets:
        store.admin.put_object(Bucket=bucket, Key=f"{store.prefix}seed", Body=b"seed")
    seen, leftovers = {}, []
    for role in contract_roles():
        if role == ADMIN:
            continue
        for bucket in store.buckets:
            for action in OBJECT_ACTIONS:
                seen[(role, bucket, action)] = observe(store, role, bucket, action)
                key = f"{store.prefix}{role}/{action}"
                if action in ("put", "multipart") and seen[(role, bucket, action)] == REFUSED:
                    if store.exists(bucket, key):
                        leftovers.append(f"{bucket}/{key}")
    return seen, leftovers


def expected(role: str, bucket: str, action: str) -> str:
    return ALLOWED if action in contract_roles()[role].get(bucket, []) else REFUSED


def matrix_differences(seen: dict) -> list[str]:
    return [
        f"{role} {action} on {bucket}: expected {expected(role, bucket, action)}, got {got}"
        for (role, bucket, action), got in seen.items()
        if got != expected(role, bucket, action)
    ]


def run_matrix(store: Store, report: Report) -> None:
    seen, leftovers = observe_matrix(store)
    for role in (r for r in contract_roles() if r != ADMIN):
        wrong = [d for d in matrix_differences({k: v for k, v in seen.items() if k[0] == role})]
        cells = sum(k[0] == role for k in seen)
        report.line(
            "FAIL" if wrong else "ok", f"access matrix {role}", "; ".join(wrong) or f"{cells} cells"
        )
    refused = sum(v == REFUSED for k, v in seen.items() if k[2] in ("put", "multipart"))
    report.line(
        "FAIL" if leftovers else "ok",
        "access wrong-role-writes-leave-nothing",
        ", ".join(leftovers) or f"{refused} refused writes",
    )


def run_credentials(store: Store, report: Report) -> None:
    role = next(r for r in contract_roles() if r != ADMIN)
    bucket = store.buckets[0]
    wrong = client(store.endpoint, Creds(store.credentials[role].access_key, "x" * 32))
    anonymous = client(store.endpoint, None)

    def refused_with_403(s3):
        for call in (
            lambda: s3.list_objects_v2(Bucket=bucket, MaxKeys=1),
            lambda: s3.put_object(Bucket=bucket, Key=f"{store.prefix}nope", Body=b"1"),
        ):
            try:
                call()
            except ClientError as error:
                assert status(error) == 403, f"status {status(error)} {error_code(error)}"
            else:
                raise AssertionError("the request was accepted")
        assert not store.exists(bucket, f"{store.prefix}nope"), "the refused write left an object"

    report.check("access wrong-secret", lambda: refused_with_403(wrong))
    report.check("access anonymous", lambda: refused_with_403(anonymous))


def classify(listed: bool, allowed: bool) -> tuple[str, str]:
    """The state of one writer's lifecycle edit against the gap recorded for the product."""
    if listed and allowed:
        return "known-gap", "the edit is accepted, as recorded"
    if listed:
        return "FAIL", "the recorded gap is closed, so the contract no longer lists it"
    if allowed:
        return "FAIL", "the edit is accepted and no gap is recorded"
    return "ok", "the edit is refused"


def edit_lifecycle(store: Store, role: str, bucket: str) -> bool:
    """True when the role replaces the lifecycle rules of a bucket, which are then restored."""
    s3 = store.as_role(role)
    with lifecycle_restored(store.admin, bucket):
        return (
            outcome(
                lambda: s3.put_bucket_lifecycle_configuration(
                    Bucket=bucket, LifecycleConfiguration={"Rules": [PROBE_RULE]}
                )
            )
            == ALLOWED
        )


def run_lifecycle_edit(store: Store, report: Report, product: str | None) -> None:
    listed = contract_gaps(product).get("lifecycle-edit-by-writer", set())
    for role, grants in contract_roles().items():
        buckets = [b for b, actions in grants.items() if "put" in actions]
        if not buckets:
            continue
        state, detail = classify(role in listed, edit_lifecycle(store, role, buckets[0]))
        name = "lifecycle-edit-by-writer" if state == "known-gap" else "access lifecycle-edit"
        report.line(state, f"{name} {role}", detail)


def run_access(store: Store, report: Report, product: str | None) -> None:
    try:
        store.create_buckets()
        run_matrix(store, report)
        run_credentials(store, report)
        run_lifecycle_edit(store, report, product)
    finally:
        store.sweep()
