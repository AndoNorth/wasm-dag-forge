"""Measures SeaweedFS with its admin server and lifecycle worker in one container.

Prints `figure <name> <value>` lines.
Environment: S8_CONTAINER, S8_PORT, S8_CREDS (the generated credentials file), S8_T0 (epoch seconds
taken just before `docker run`), S8_VOLUME_MB (the volume size limit the container started with).
Phases: provision the four buckets, the S5 contract run (base, access, lifecycle), then 100,000
objects of 1 KiB.
"""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import yaml
from botocore.config import Config
from cg import Sampler, docker, figure

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OBJECTS = 100_000
OBJECT_BYTES = 1024
WORKERS = 16
P = os.environ.get("S8_PREFIX", "store")


def s3_client(endpoint, creds):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=creds["ak"],
        aws_secret_access_key=creds["sk"],
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"}, retries={"max_attempts": 3}, max_pool_connections=64
        ),
    )


def processes(container):
    """Resident MiB of every process in the container, by command."""
    script = (
        "for d in /proc/[0-9]*; do r=$(awk '/VmRSS/ {print $2}' $d/status 2>/dev/null); "
        '[ -n "$r" ] && echo "$r $(tr "\\0" " " < $d/cmdline | cut -c1-40)"; done'
    )
    out = docker("exec", container, "sh", "-c", script)
    found = {}
    for line in out.splitlines():
        kib, _, command = line.partition(" ")
        for name in ("weed server", "weed admin", "weed worker"):
            if command.startswith(name):
                found[name.split()[1]] = int(kib) / 1024
    return found


def volumes(container):
    status = json.loads(docker("exec", container, "curl", "-s", "http://127.0.0.1:9333/dir/status"))
    nodes = [
        node
        for dc in status["Topology"]["DataCenters"]
        for rack in dc["Racks"]
        for node in rack["DataNodes"]
    ]
    return sum(node["Volumes"] for node in nodes)


def disk(container):
    kib = int(docker("exec", container, "du", "-sk", "/data").split()[0])
    return kib / 1024


def report_processes(container, label):
    for name, mib in processes(container).items():
        figure(f"{P}.rss_mib.{name}.{label}", f"{mib:.0f}")
    figure(f"{P}.volumes.{label}", volumes(container))
    figure(f"{P}.data_mib.{label}", f"{disk(container):.0f}")


def contract(mode, endpoint, extra=()):
    run = subprocess.run(
        [
            "uv",
            "run",
            "--no-project",
            "--quiet",
            "--with",
            "boto3==1.43.119",
            "python",
            str(HERE / "s3_contract.py"),
            "--endpoint",
            endpoint,
            mode,
            *extra,
        ],
        capture_output=True,
        text=True,
    )
    lines = run.stdout.splitlines()
    summary = [line for line in lines if line.startswith("SUMMARY")]
    passed = sum(line.startswith("PASS") for line in lines)
    failed = [line for line in lines if line.startswith("FAIL")]
    for line in failed:
        print(f"contract {mode}: {line}", file=sys.stderr)
    figure(f"{P}.contract.{mode}.pass", passed)
    figure(f"{P}.contract.{mode}.fail", len(failed))
    if not summary:
        print(run.stderr[-2000:], file=sys.stderr)
    return failed


def main():
    container, port = os.environ["S8_CONTAINER"], os.environ["S8_PORT"]
    endpoint = f"http://127.0.0.1:{port}"
    creds = json.loads(Path(os.environ["S8_CREDS"]).read_text())
    t0 = float(os.environ["S8_T0"])
    admin = s3_client(endpoint, creds["admin"])
    while True:
        try:
            admin.list_buckets()
            break
        except Exception:
            time.sleep(0.2)
    figure(f"{P}.start_s3_ready_s", f"{time.time() - t0:.1f}")
    while (
        docker(
            "exec",
            container,
            "curl",
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "http://127.0.0.1:12535/",
            check=False,
        )
        != "200"
    ):
        time.sleep(0.5)
    figure(f"{P}.start_admin_ready_s", f"{time.time() - t0:.1f}")
    figure(f"{P}.volume_size_limit_mb", os.environ["S8_VOLUME_MB"])
    sampler = Sampler(container)
    sampler.start()
    sampler.phase = "idle"
    time.sleep(30)
    report_processes(container, "idle")

    sampler.phase = "provision"
    buckets = yaml.safe_load((ROOT / "contracts/artifact/layout.yaml").read_text())["buckets"]
    for bucket in buckets:
        admin.create_bucket(Bucket=bucket)
        admin.put_object(Bucket=bucket, Key="provision/seed", Body=b"seed")
    time.sleep(5)
    report_processes(container, "provisioned")

    if not os.environ.get("S8_SKIP_CONTRACT"):
        contract_runs(container, endpoint, sampler)

    sampler.phase = "objects"
    body = os.urandom(OBJECT_BYTES)
    start = time.monotonic()
    stored = []

    def put(i):
        key = f"tenant{i % 8}/batch{i // 1000}/job{i}/{i}/compile/r0a1/result.bin"
        client.put_object(Bucket="wdf-results", Key=key, Body=body)
        stored.append(i)

    # One client for all threads, since a boto3 client is thread safe.
    client = s3_client(endpoint, creds["admin"])
    failed = False
    pool = ThreadPoolExecutor(WORKERS)
    try:
        list(pool.map(put, range(OBJECTS)))
    except Exception as exc:
        failed = True
        pool.shutdown(wait=True, cancel_futures=True)
        print(f"objects: stopped after {len(stored)}: {type(exc).__name__}", file=sys.stderr)
    seconds = time.monotonic() - start
    figure(f"{P}.objects_stored", len(stored))
    figure(f"{P}.objects_failed", "yes" if failed else "no")
    figure(f"{P}.objects_put_s", f"{seconds:.0f}")
    figure(f"{P}.objects_put_rate_s", f"{len(stored) / seconds:.0f}")
    sampler.phase = "settle"
    time.sleep(30)
    if not failed:
        report_processes(container, "after_objects")
        pages = client.get_paginator("list_objects_v2").paginate(
            Bucket="wdf-results", Prefix="tenant"
        )
        figure(f"{P}.objects_listed", sum(page["KeyCount"] for page in pages))
    sampler.phase = "idle_after"
    time.sleep(15)
    sampler.stop()
    sampler.report(P)
    phases = ("provision", "contract_base", "contract_access", "contract_lifecycle")
    load = [sampler.peaks(p)["ws"] for p in (*phases, "objects", "settle") if sampler.peaks(p)]
    figure(f"{P}.peak_ws_mib.load", f"{max(load):.0f}")


def contract_runs(container, endpoint, sampler):
    for mode, extra in (
        ("base", ()),
        ("access", ()),
        ("lifecycle", ("--rule", "date", "--timeout", "300")),
    ):
        sampler.phase = f"contract_{mode}"
        if mode == "lifecycle":
            # A one minute detection interval so the rule is acted on while the run waits.
            config = (
                '{"admin_runtime":{"enabled":true,"detection_interval_minutes":1,'
                '"detection_timeout_seconds":60,"max_jobs_per_detection":1}}'
            )
            url = "http://127.0.0.1:12535/api/plugin/job-types/s3_lifecycle/config"
            docker(
                "exec",
                container,
                "curl",
                "-s",
                "-X",
                "PUT",
                "-H",
                "Content-Type: application/json",
                "-d",
                config,
                url,
            )
        contract(mode, endpoint, extra)
        report_processes(container, f"after_{mode}")


if __name__ == "__main__":
    sys.exit(main())
