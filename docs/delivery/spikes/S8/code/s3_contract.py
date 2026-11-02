# ruff: noqa: B023, E501  (the closures run in the iteration that binds their variables, and detail strings are long)
"""S3 contract script for the wdf object store.

  s3_contract.py --endpoint http://127.0.0.1:PORT base|access|lifecycle [opts]

Every check prints `PASS|FAIL <id> <detail>`; exit code is the number of FAILs.
The role matrix comes from roles.json and the credentials, generated for each run, from the JSON file that
S8_CREDS names (`{"admin": {"ak", "sk"}, "<role>": {"ak", "sk"}}`).
Taken from the object store spike of the same name, with the credentials moved out of the file.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import sys
import time
import uuid

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
)

R = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles.json")))
CREDS = json.load(open(os.environ["S8_CREDS"]))
R["admin"] = CREDS["admin"]
for _name, _role in R["roles"].items():
    _role.update(CREDS[_name])
BUCKETS = R["buckets"]
fails = 0


def client(ep, ak, sk, **cfg):
    return boto3.client(
        "s3",
        endpoint_url=ep,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 1},
            connect_timeout=5,
            read_timeout=60,
            **cfg,
        ),
    )


def admin(ep, **cfg):
    return client(ep, R["admin"]["ak"], R["admin"]["sk"], **cfg)


def role_client(ep, name):
    return client(ep, R["roles"][name]["ak"], R["roles"][name]["sk"])


def rec(ok, cid, detail=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'} {cid} {detail}", flush=True)


def code(e):
    if isinstance(e, ClientError):
        return (
            f"{e.response['Error'].get('Code')}/{e.response['ResponseMetadata']['HTTPStatusCode']}"
        )
    return type(e).__name__


def attempt(fn):
    try:
        return True, fn()
    except Exception as e:  # noqa: BLE001 - the error class is the evidence
        return False, e


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def b64sha(b):
    import base64

    return base64.b64encode(hashlib.sha256(b).digest()).decode()


def ensure_buckets(s3, names):
    for b in names:
        ok, e = attempt(lambda: s3.create_bucket(Bucket=b))
        if (
            not ok
            and "BucketAlreadyOwnedByYou" not in code(e)
            and "BucketAlreadyExists" not in code(e)
        ):
            rec(False, f"setup.create_bucket.{b}", code(e))


# ---------------------------------------------------------------- base: the 14 earlier checks
def base(ep):
    s3 = admin(ep)
    s3_wr = admin(
        ep,
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    b = "wdf-contract-base"
    data = os.urandom(1 << 20)

    def chk(cid, fn, want=None):
        ok, r = attempt(fn)
        rec(ok and (want is None or want(r)), cid, "" if ok else code(r))
        return r if ok else None

    chk("B01 create_bucket", lambda: s3.create_bucket(Bucket=b))
    chk(
        "B02 put default boto3 checksum (CRC32 trailer)",
        lambda: s3.put_object(Bucket=b, Key="k/default", Body=data),
    )
    chk(
        "B03 put with correct ChecksumSHA256",
        lambda: s3.put_object(
            Bucket=b,
            Key="k/sha",
            Body=data,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=b64sha(data),
        ),
    )
    chk(
        "B04 head returns ChecksumSHA256",
        lambda: s3.head_object(Bucket=b, Key="k/sha", ChecksumMode="ENABLED"),
        lambda r: r.get("ChecksumSHA256") == b64sha(data),
    )
    ok, e = attempt(
        lambda: s3.put_object(
            Bucket=b,
            Key="k/bad",
            Body=data,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=b64sha(b"other"),
        )
    )
    gone = not attempt(lambda: s3.head_object(Bucket=b, Key="k/bad"))[0]
    rec(
        (not ok) and gone,
        "B05 put with WRONG ChecksumSHA256 rejected and nothing stored",
        f"{code(e) if not ok else 'accepted'} object_absent={gone}",
    )
    chk(
        "B06 conditional put If-None-Match:* first",
        lambda: s3.put_object(Bucket=b, Key="k/once", Body=b"1", IfNoneMatch="*"),
    )
    ok, e = attempt(lambda: s3.put_object(Bucket=b, Key="k/once", Body=b"2", IfNoneMatch="*"))
    keep = s3.get_object(Bucket=b, Key="k/once")["Body"].read() == b"1"
    rec(
        (not ok) and "PreconditionFailed" in code(e) and keep,
        "B07 conditional put second fails, first kept",
        f"{code(e) if not ok else 'accepted'} first_kept={keep}",
    )
    big = os.urandom(12 << 20)
    tc = TransferConfig(multipart_threshold=5 << 20, multipart_chunksize=5 << 20)
    ok, e = attempt(lambda: s3.upload_fileobj(io.BytesIO(big), b, "k/mpu", Config=tc))
    h = attempt(lambda: s3.head_object(Bucket=b, Key="k/mpu", ChecksumMode="ENABLED"))
    rec(
        ok and h[0] and h[1]["ContentLength"] == len(big),
        "B08 multipart 12MiB upload + head",
        f"etag={h[1]['ETag'] if h[0] else code(h[1])}",
    )
    ok, e = attempt(
        lambda: hashlib.sha256(s3.get_object(Bucket=b, Key="k/mpu")["Body"].read()).hexdigest()
    )
    rec(ok and e == sha256(big), "B09 streamed download sha256 matches", "" if ok else code(e))
    lc = {
        "Rules": [
            {
                "ID": "jobs-expire",
                "Status": "Enabled",
                "Filter": {"Prefix": "jobs/"},
                "Expiration": {"Days": 7},
            },
            {
                "ID": "abort-mpu",
                "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
            },
        ]
    }
    chk(
        "B10 put_bucket_lifecycle (prefix expiry 7d + abort MPU 1d)",
        lambda: s3.put_bucket_lifecycle_configuration(Bucket=b, LifecycleConfiguration=lc),
    )
    chk(
        "B11 get_bucket_lifecycle returns rules",
        lambda: s3.get_bucket_lifecycle_configuration(Bucket=b),
        lambda r: {x["ID"] for x in r["Rules"]} >= {"jobs-expire"},
    )
    chk(
        "B12 object tagging",
        lambda: (
            s3.put_object_tagging(
                Bucket=b, Key="k/sha", Tagging={"TagSet": [{"Key": "a", "Value": "b"}]}
            ),
            s3.get_object_tagging(Bucket=b, Key="k/sha"),
        )[1],
        lambda r: r["TagSet"] == [{"Key": "a", "Value": "b"}],
    )

    def listdel():
        for i in range(4):
            s3.put_object(Bucket=b, Key=f"del/{i}", Body=b"x")
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=b, Prefix="del/").get("Contents", [])]
        r = s3.delete_objects(Bucket=b, Delete={"Objects": [{"Key": k} for k in keys]})
        left = s3.list_objects_v2(Bucket=b, Prefix="del/").get("KeyCount", 0)
        return len(keys), len(r.get("Deleted", [])), len(r.get("Errors", [])), left

    chk("B13 list prefix + batch delete", listdel, lambda r: r == (4, 4, 0, 0))
    chk(
        "B14 put with when_required checksum config",
        lambda: s3_wr.put_object(Bucket=b, Key="k/wr", Body=data),
        lambda r: s3.get_object(Bucket=b, Key="k/wr")["Body"].read() == data,
    )


# ---------------------------------------------------------------- access: per-bucket credentials
def access(ep):
    adm = admin(ep)
    ensure_buckets(adm, BUCKETS)
    for bk in BUCKETS:
        adm.put_object(Bucket=bk, Key="probe/seed", Body=b"seed")
    n = 0
    for name, role in R["roles"].items():
        s3 = role_client(ep, name)
        for bk in BUCKETS:
            canw, canr = bk in role["write"], bk in role["read"]
            key = f"probe/{name}-{uuid.uuid4().hex[:8]}"
            ok, e = attempt(lambda: s3.put_object(Bucket=bk, Key=key, Body=b"payload"))
            leaked = attempt(lambda: adm.head_object(Bucket=bk, Key=key))[0]
            if canw:
                rec(ok and leaked, f"A.{name}.put.{bk} allowed", "" if ok else code(e))
            else:
                rec(
                    (not ok) and "AccessDenied" in code(e) and not leaked,
                    f"A.{name}.put.{bk} refused",
                    f"{code(e) if not ok else 'ACCEPTED'} object_leaked={leaked}",
                )
            ok, e = attempt(lambda: s3.get_object(Bucket=bk, Key="probe/seed")["Body"].read())
            rec(
                ok == canr and (ok or "AccessDenied" in code(e)),
                f"A.{name}.get.{bk} {'allowed' if canr else 'refused'}",
                "" if ok else code(e),
            )
            ok, e = attempt(lambda: s3.list_objects_v2(Bucket=bk, Prefix="probe/"))
            rec(
                ok == canr and (ok or "AccessDenied" in code(e)),
                f"A.{name}.list.{bk} {'allowed' if canr else 'refused'}",
                "" if ok else code(e),
            )
            ok, e = attempt(lambda: s3.create_multipart_upload(Bucket=bk, Key=key + "-mpu"))
            if ok:
                s3.abort_multipart_upload(Bucket=bk, Key=key + "-mpu", UploadId=e["UploadId"])
            rec(
                ok == canw and (ok or "AccessDenied" in code(e)),
                f"A.{name}.multipart.{bk} {'allowed' if canw else 'refused'}",
                "" if ok else code(e),
            )
            if canw:
                ok, e = attempt(lambda: s3.delete_object(Bucket=bk, Key=key))
                rec(ok, f"A.{name}.delete.{bk} allowed", "" if ok else code(e))
            n += 1
        ok, e = attempt(
            lambda: s3.put_bucket_lifecycle_configuration(
                Bucket=role["write"][0],
                LifecycleConfiguration={
                    "Rules": [
                        {
                            "ID": "x",
                            "Status": "Enabled",
                            "Filter": {"Prefix": ""},
                            "Expiration": {"Days": 1},
                        }
                    ]
                },
            )
        )
        rec(
            (not ok) and "AccessDenied" in code(e),
            f"A.{name}.put_lifecycle refused (not admin)",
            code(e) if not ok else "ACCEPTED",
        )
        ok, e = attempt(lambda: s3.create_bucket(Bucket=f"wdf-rogue-{name}"))
        rec(not ok, f"A.{name}.create_bucket refused", code(e) if not ok else "ACCEPTED")
    bad = client(ep, R["roles"]["submit"]["ak"], "wrongsecretwrongsecretwrongsecret00")
    ok, e = attempt(lambda: bad.put_object(Bucket="wdf-sources", Key="probe/badsecret", Body=b"x"))
    rec(not ok, "A.badsecret.put refused", code(e) if not ok else "ACCEPTED")
    anon = boto3.client(
        "s3",
        endpoint_url=ep,
        region_name="us-east-1",
        config=Config(
            signature_version="UNSIGNED",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 1},
        ),
    )
    ok, e = attempt(lambda: anon.put_object(Bucket="wdf-sources", Key="probe/anon", Body=b"x"))
    rec(not ok, "A.anonymous.put refused", code(e) if not ok else "ACCEPTED")
    ok, e = attempt(lambda: anon.get_object(Bucket="wdf-sources", Key="probe/seed"))
    rec(not ok, "A.anonymous.get refused", code(e) if not ok else "ACCEPTED")


# ---------------------------------------------------------------- lifecycle: expiry actually observed
def lifecycle(ep, timeout, mode):
    s3 = admin(ep)
    ensure_buckets(s3, BUCKETS)
    tag = uuid.uuid4().hex[:6]
    now = dt.datetime.now(dt.UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    exp = {"Date": midnight} if mode == "date" else {"Days": 1}
    # rule only on wdf-results prefix exp/, nothing on wdf-checkpoints or wdf-results prefix keep/
    ok, e = attempt(
        lambda: s3.put_bucket_lifecycle_configuration(
            Bucket="wdf-results",
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "short",
                        "Status": "Enabled",
                        "Filter": {"Prefix": f"exp-{tag}/"},
                        "Expiration": exp,
                    },
                    {
                        "ID": "abort-mpu",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                    },
                ]
            },
        )
    )
    rec(ok, f"L01 put rule on wdf-results ({mode})", "" if ok else code(e))
    if not ok:
        return
    got = s3.get_bucket_lifecycle_configuration(Bucket="wdf-results")["Rules"]
    rec(
        any(r["ID"] == "short" for r in got),
        "L02 rule read back",
        json.dumps(got, default=str)[:300],
    )
    targets = {("wdf-results", f"exp-{tag}/a"), ("wdf-results", f"exp-{tag}/b")}
    controls = {
        ("wdf-results", f"keep-{tag}/a"),
        ("wdf-checkpoints", f"exp-{tag}/a"),
        ("wdf-sources", f"exp-{tag}/a"),
    }
    for bk, k in targets | controls:
        s3.put_object(Bucket=bk, Key=k, Body=b"x" * 1024)
    t0 = time.time()
    gone = {}
    while time.time() - t0 < timeout and len(gone) < len(targets):
        for bk, k in targets - set(gone):
            if not attempt(lambda: s3.head_object(Bucket=bk, Key=k))[0]:
                gone[(bk, k)] = round(time.time() - t0)
        time.sleep(5)
    rec(
        len(gone) == len(targets),
        f"L03 targeted objects expired ({mode}) within {timeout}s",
        f"gone_after_s={sorted(gone.values())}",
    )
    still = [c for c in controls if attempt(lambda: s3.head_object(Bucket=c[0], Key=c[1]))[0]]
    rec(
        len(still) == len(controls),
        "L04 objects outside the rule (other prefix, other buckets) kept",
        f"kept={len(still)}/{len(controls)}",
    )
    listed = [
        o["Key"]
        for o in s3.list_objects_v2(Bucket="wdf-results", Prefix=f"exp-{tag}/").get("Contents", [])
    ]
    rec(not listed, "L05 expired keys also gone from listing", f"listed={listed}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", required=True)
    p.add_argument("mode", choices=["base", "access", "lifecycle"])
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--rule", choices=["date", "days"], default="date")
    a = p.parse_args()
    {
        "base": lambda: base(a.endpoint),
        "access": lambda: access(a.endpoint),
        "lifecycle": lambda: lifecycle(a.endpoint, a.timeout, a.rule),
    }[a.mode]()
    print(f"SUMMARY mode={a.mode} fails={fails}")
    sys.exit(min(fails, 100))
