"""Loads the app Postgres with TimescaleDB with the standard load.

Prints `figure <name> <value>` lines.

Environment: S8_CONTAINER, S8_PORT, S8_PASSWORD, S8_T0 (epoch seconds just before `docker run`).
The load is 100,000 jobs of two tasks, so 100,000 accepted rows and 14 event rows per job.
"""

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import wdf_keys
from cg import Sampler, docker, figure

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "contracts/events/vectors/golden"
JOBS = 100_000
SPAN = timedelta(days=7)
BATCH_SIZES = (1000, 5000, 20000)
EVENT_BATCH = 5000
RATE_JOBS = 20_000
TENANTS = ["acme", "globex", "initech", "umbrella", "hooli", "stark", "wayne", "wonka"]
# Six job events and four per task: the names stand in for the mix, the byte size is measured.
JOB_EVENTS = [
    "job.accepted",
    "job.published",
    "job.admitted",
    "job.started",
    "job.succeeded",
    "job.paused",
]
TASK_EVENTS = ["task.placed", "task.started", "task.result.ready", "task.succeeded"]
TASKS = ["compile", "sim"]
GOLDEN_JOB = "3a9617fa-df7c-5760-a644-42d559e0f433"
GOLDEN_BATCH = "20e38fea-a41a-57cd-b87a-f6711349a05d"

DDL = """
create extension if not exists timescaledb;
create table accepted_job (
    job_id uuid primary key, tenant text not null, submission_key text not null,
    batch_id uuid not null, priority_class text not null, job_class text not null,
    status text not null default 'accepted', manifest_ref jsonb not null,
    manifest_sha256 text generated always as (manifest_ref ->> 'sha256') stored,
    schema_version text not null, traceparent text not null, tracestate text,
    published_at timestamptz, seed bigint not null, parameters jsonb not null,
    fingerprint text not null, requested_action text,
    control_seq integer not null default 0, control_delivered_at timestamptz,
    pending_patch_ref jsonb, created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(), terminal_at timestamptz,
    unique (tenant, submission_key));
create index accepted_job_unpublished on accepted_job (created_at) where published_at is null;
create table event (
    event_id text not null, event_time timestamptz not null, type text not null,
    source text not null, tenant text not null, job_id uuid not null, task_id text,
    attempt integer, body text not null, unique (event_id, event_time));
select create_hypertable('event', 'event_time', chunk_time_interval => interval '1 day');
create index event_job on event (job_id, event_time);
alter table event set (timescaledb.compress, timescaledb.compress_segmentby = 'job_id',
    timescaledb.compress_orderby = 'event_time');
"""

INSERT_JOBS = """
insert into {table} (job_id, tenant, submission_key, batch_id, priority_class, job_class,
    manifest_ref, schema_version, traceparent, seed, parameters, fingerprint, created_at,
    updated_at)
select a, b, c, d, e, f, g::jsonb, h, i, j, k::jsonb, l, m, m
from unnest(%s::uuid[], %s::text[], %s::text[], %s::uuid[], %s::text[], %s::text[],
    %s::text[], %s::text[], %s::text[], %s::bigint[], %s::text[], %s::text[],
    %s::timestamptz[]) as t(a, b, c, d, e, f, g, h, i, j, k, l, m)
on conflict (tenant, submission_key) do nothing"""

INSERT_EVENTS = """
insert into {table} (event_id, event_time, type, source, tenant, job_id, task_id, attempt, body)
select * from unnest(%s::text[], %s::timestamptz[], %s::text[], %s::text[], %s::text[],
    %s::uuid[], %s::text[], %s::int[], %s::text[]) on conflict (event_id, event_time) do nothing"""


def templates():
    out = {}
    for kind in JOB_EVENTS + TASK_EVENTS:
        doc = json.loads((GOLDEN / kind / "1.0.json").read_text())
        out[kind] = (
            doc,
            json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
    return out


TEMPLATES = templates()
START = datetime.now(UTC) - SPAN


def job_rows(i):
    """The accepted row and 14 event rows of job i, created 6 seconds apart over the 7 days."""
    tenant = TENANTS[i % len(TENANTS)]
    key = f"sub-{i}"
    job = wdf_keys.job_id(tenant, key)
    batch = wdf_keys.batch_id(tenant, f"batch-{i // 1000}")
    created = START + SPAN * i / JOBS
    sha = f"{i:064x}"
    manifest = json.dumps(
        {
            "kind": "manifest",
            "name": "manifest.json",
            "sha256": sha,
            "size": 2048,
            "uri": f"s3://wdf-sources/sha256/{sha[:2]}/{sha}",
        },
        separators=(",", ":"),
    )
    row = (
        job,
        tenant,
        key,
        batch,
        "standard",
        "short",
        manifest,
        "urn:wdf:schema:job.accepted:1.0",
        f"00-4bf92f3577b34da6a3ce929d0e0e4736-{i:016x}-01",
        i,
        json.dumps({"class_override": "short"}),
        uuid.uuid5(uuid.NAMESPACE_URL, job).hex * 2,
        created,
    )
    events = []
    seq = 0
    plan = [(k, None) for k in JOB_EVENTS] + [(k, t) for t in TASKS for k in TASK_EVENTS]
    for kind, task in plan:
        doc, text = TEMPLATES[kind]
        moment = created + timedelta(milliseconds=400 * seq, microseconds=(i * 7 + seq * 13) % 1000)
        stamp = moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        event_id = wdf_keys.cloudevent_id(job, task or "", "r0a1" if task else "", kind, seq)
        body = (
            text.replace(doc["id"], event_id)
            .replace(doc["time"], stamp)
            .replace(GOLDEN_JOB, job)
            .replace(GOLDEN_BATCH, batch)
            .replace('"sub-1"', f'"{key}"')
            .replace('"tenant":"acme"', f'"tenant":"{tenant}"')
        )
        body = body.replace("4bf92f3577b34da6a3ce929d0e0e4736", job.replace("-", "")).replace(
            "00f067aa0ba902b7", event_id[:8] + event_id[-8:]
        )
        if task:
            was = doc["taskid"]
            body = (
                body.replace(f'"taskid":"{was}"', f'"taskid":"{task}"')
                .replace(f'/{was}"', f'/{task}"')
                .replace(f'"task":"{was}"', f'"task":"{task}"')
                .replace('"attempt":2', '"attempt":1')
                .replace("acme/b/j/7/sim/r0a2", f"{tenant}/{batch}/{job}/{i}/{task}/r0a1")
                .replace("r0a2", "r0a1")
            )
        events.append(
            (
                f"{doc['source']}#{event_id}",
                moment,
                doc["type"],
                doc["source"],
                tenant,
                job,
                task,
                1 if task else None,
                body,
            )
        )
        seq += 1
    return row, events


def columns(rows):
    return [list(c) for c in zip(*rows, strict=True)]


def timed(fn):
    t = time.monotonic()
    fn()
    return time.monotonic() - t


def sizes(conn, label, container):
    conn.execute("checkpoint")
    row = conn.execute(
        "select pg_total_relation_size('accepted_job'), hypertable_size('event'), "
        "pg_database_size(current_database())"
    ).fetchone()
    jobs_bytes, event_bytes, db_bytes = row
    du = int(docker("exec", container, "du", "-sk", "/var/lib/postgresql/data").split()[0]) * 1024
    wal = (
        int(docker("exec", container, "du", "-sk", "/var/lib/postgresql/data/pg_wal").split()[0])
        * 1024
    )
    figure(f"pg.bytes.{label}.accepted_job", jobs_bytes)
    figure(f"pg.bytes.{label}.event", event_bytes)
    figure(f"pg.bytes_per_job.{label}", f"{(jobs_bytes + event_bytes) / JOBS:.0f}")
    figure(f"pg.bytes_per_job.{label}.accepted_job", f"{jobs_bytes / JOBS:.0f}")
    figure(f"pg.bytes_per_job.{label}.event", f"{event_bytes / JOBS:.0f}")
    figure(f"pg.database_mib.{label}", f"{db_bytes / 1048576:.0f}")
    figure(f"pg.pgdata_mib.{label}", f"{du / 1048576:.0f}")
    figure(f"pg.pg_wal_mib.{label}", f"{wal / 1048576:.0f}")


def wait_ready(dsn, container, deadline=180):
    end = time.monotonic() + deadline
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                conn.execute("select 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > end:
                raise
            time.sleep(0.1)


def main():
    container, port = os.environ["S8_CONTAINER"], os.environ["S8_PORT"]
    password = os.environ["S8_PASSWORD"]
    dsn = f"host=127.0.0.1 port={port} user=postgres password={password} dbname=postgres"
    t0 = float(os.environ["S8_T0"])
    wait_ready(dsn, container)
    figure("pg.start_cold_s", f"{time.time() - t0:.1f}")
    sampler = Sampler(container)
    sampler.start()
    sampler.phase = "idle"
    time.sleep(10)
    conn = psycopg.connect(dsn, autocommit=False)
    sizes_label = "empty"
    conn.execute(DDL)
    conn.commit()
    for name in (
        "shared_buffers",
        "max_connections",
        "work_mem",
        "effective_cache_size",
        "maintenance_work_mem",
        "max_wal_size",
        "wal_buffers",
        "timescaledb.max_background_workers",
        "timescaledb.telemetry_level",
    ):
        figure(
            f"pg.setting.{name}",
            conn.execute("select current_setting(%s)", (name,)).fetchone()[0].replace(" ", ""),
        )
    figure("pg.version", conn.execute("show server_version").fetchone()[0].split()[0])
    figure(
        "pg.timescaledb_version",
        conn.execute(
            "select extversion from pg_extension where extname = 'timescaledb'"
        ).fetchone()[0],
    )
    sizes(conn, sizes_label, container)

    # Generate once, then time only the database work.
    t = time.monotonic()
    generated = [job_rows(i) for i in range(JOBS)]
    figure("pg.generate_s", f"{time.monotonic() - t:.0f}")
    job_cols = [columns([g[0] for g in generated[s : s + 1000]]) for s in range(0, JOBS, 1000)]
    all_events = [e for g in generated for e in g[1]]
    figure("pg.event_rows", len(all_events))
    figure("pg.event_body_bytes_avg", f"{sum(len(e[8]) for e in all_events) / len(all_events):.0f}")

    sampler.phase = "rows"

    def load_rows():
        for cols in job_cols:
            conn.execute(INSERT_JOBS.format(table="accepted_job"), cols)
            conn.commit()

    figure("pg.rows_load_s", f"{timed(load_rows):.1f}")

    sampler.phase = "rowlife"
    ids = [g[0][0] for g in generated]

    def lifecycle():
        # published, admitted, running and succeeded: four updates of every row.
        steps = [
            ("published_at = now()", None),
            ("status = 'admitted'", None),
            ("status = 'running'", None),
            ("status = 'succeeded', terminal_at = now()", None),
        ]
        for assignment, _ in steps:
            for s in range(0, JOBS, 1000):
                conn.execute(
                    f"update accepted_job set {assignment}, updated_at = now() "
                    "where job_id = any(%s::uuid[])",
                    (ids[s : s + 1000],),
                )
                conn.commit()

    figure("pg.rowlife_s", f"{timed(lifecycle):.1f}")

    sampler.phase = "events"
    inserted = 0

    def load_events():
        nonlocal inserted
        for s in range(0, len(all_events), EVENT_BATCH):
            cur = conn.execute(
                INSERT_EVENTS.format(table="event"), columns(all_events[s : s + EVENT_BATCH])
            )
            inserted += cur.rowcount
            conn.commit()

    seconds = timed(load_events)
    figure("pg.events_load_s", f"{seconds:.1f}")
    figure("pg.events_inserted", inserted)
    figure("pg.events_load_rate_s", f"{inserted / seconds:.0f}")
    figure(
        "pg.chunks",
        conn.execute(
            "select count(*) from timescaledb_information.chunks where hypertable_name = 'event'"
        ).fetchone()[0],
    )

    sampler.phase = "settle"
    time.sleep(10)
    sizes(conn, "uncompressed", container)

    def chunks(where):
        return [
            r[0]
            for r in conn.execute(
                "select format('%I.%I', chunk_schema, chunk_name) "
                "from timescaledb_information.chunks "
                f"where hypertable_name = 'event' and not is_compressed and {where} "
                "order by range_start"
            )
        ]

    sampler.phase = "compress_old"
    old = chunks("range_end < now() - interval '2 days'")

    def compress(names):
        for name in names:
            conn.execute("select compress_chunk(%s::regclass)", (name,))
            conn.commit()

    figure("pg.chunks_compressed_old", len(old))
    figure("pg.compress_old_s", f"{timed(lambda: compress(old)):.1f}")
    sampler.phase = "settle"
    time.sleep(5)
    sizes(conn, "compressed_old", container)

    sampler.phase = "compress_all"
    rest = chunks("true")
    figure("pg.chunks_compressed_rest", len(rest))
    figure("pg.compress_rest_s", f"{timed(lambda: compress(rest)):.1f}")
    sampler.phase = "settle"
    time.sleep(5)
    sizes(conn, "compressed_all", container)
    ratio = conn.execute(
        "select (before_compression_total_bytes::numeric / after_compression_total_bytes)"
        "::numeric(6,2) "
        "from hypertable_compression_stats('event')"
    ).fetchone()[0]
    figure("pg.event_ratio_compressed_all", ratio)

    sampler.phase = "rates"
    conn.execute("create table rate_job (like accepted_job including all)")
    conn.execute("create table rate_event (like event including all)")
    conn.execute(
        "select create_hypertable('rate_event', 'event_time', "
        "chunk_time_interval => interval '1 day')"
    )
    conn.commit()
    for size in BATCH_SIZES:
        conn.execute("truncate rate_job")
        conn.execute("truncate rate_event")
        conn.commit()

        def rows_sweep(size=size):
            for s in range(0, JOBS, size):
                part = columns([g[0] for g in generated[s : s + size]])
                conn.execute(INSERT_JOBS.format(table="rate_job"), part)
                conn.commit()

        figure(f"pg.insert_rate_rows_s.{size}", f"{JOBS / timed(rows_sweep):.0f}")
        sample = all_events[: RATE_JOBS * 14]

        def events_sweep(size=size, sample=sample):
            for s in range(0, len(sample), size):
                conn.execute(
                    INSERT_EVENTS.format(table="rate_event"), columns(sample[s : s + size])
                )
                conn.commit()

        figure(f"pg.ingest_rate_events_s.{size}", f"{len(sample) / timed(events_sweep):.0f}")
    conn.execute("drop table rate_job, rate_event")
    conn.commit()

    sampler.phase = "idle_after"
    time.sleep(15)
    sampler.stop()
    sampler.report("pg")
    load = [
        sampler.peaks(p)["ws"]
        for p in ("rows", "rowlife", "events", "settle", "compress_old", "compress_all")
        if sampler.peaks(p)
    ]
    figure("pg.peak_ws_mib.load", f"{max(load):.0f}")
    conn.close()

    # Restart time with the loaded database, a clean shutdown and start again.
    t = time.time()
    docker("restart", container)
    wait_ready(dsn, container)
    figure("pg.start_restart_loaded_s", f"{time.time() - t:.1f}")


if __name__ == "__main__":
    sys.exit(main())
