# Spike: footprint of the real state components (S8)

Run on 2026-11-01.
Owner: the [durable state and artifact storage epic](https://github.com/AndoNorth/wasm-dag-forge/issues/3), lane M.
Breakdown and method: [docs/epics/05-state-storage.md](../epics/05-state-storage.md), section 4 (M.1) and [docs/method.md](../method.md).
Throwaway code: `spikes/S8/`, run as `nix develop -c spikes/S8/run.sh`.
Every number below is a `figure <name> <value>` line of that run, and the name is given in backticks beside it.

## Result

**Pass.**
The infra node is 5,120 MiB for both `ci` and `dev`, which gives a `wdf-infra` quota of 4,772 MiB.
The sum of the limits of the five infra components (3,840 MiB) is at most that quota, the sum of the peaks of those components and the control reserve plus 20 percent (4,416 MiB) is at most the 4,494 MiB that the node allocates after the 50 MiB of daemons, and the `ci` sum of node limits is 10,880 MiB, at most 15,984 MiB.
The smallest infra node in steps of 128 MiB that passes is 5,120 MiB, and 4,992 MiB fails the limits line by 106 MiB.

Two findings change module work and are not part of the pass line.

1. SeaweedFS as started in the S5 spike (`weed server -s3`) was OOM killed at a 1 GiB cap after 87,809 of the 100,000 objects.
   Bounding the Go heap with `GOMEMLIMIT=512MiB` stored all 100,000 objects at a working set peak of 533 MiB.
   The module must set `GOMEMLIMIT`, as the Temporal spike found for its caches.
2. The `ci` infra node of 3,072 MiB cannot hold the components at their limits, and `dev` at 4,096 MiB cannot either, so R.1 applies the figure above to both.

## Question

What do the real app Postgres with TimescaleDB and the object store with its admin server and lifecycle worker need in memory and disk for the standard load, and what infra node and `wdf-infra` quota do they, Temporal, its Postgres and RabbitMQ need together?
The standard load is 100,000 jobs of two tasks, so 100,000 accepted rows in chunks of 1,000 and 14 event rows per job.

## Environment

| Item | Value |
| --- | --- |
| Host | WSL2, 20,002 MiB `MemTotal` (`host.mem_total_mib`), 8 CPUs (`host.cpus`), kernel 6.6.87.2, Docker 29.1.3 |
| Other work on the host | 14 other containers running (`host.other_containers`), not touched, 14,314 MiB `MemAvailable` before (`host.mem_available_mib.before`) and 14,431 MiB after (`host.mem_available_mib.after`) |
| Locks | `/tmp/wdf-cluster.lock` for the whole run and one of the two `wdf-t1` lock files |
| Containers | one at a time, `--memory 1g --memory-swap 1g`, published on 127.0.0.1 only, ports 19020 (Postgres) and 19021 (object store) of worker slot 2, label `wdf.dev/verify-run` |
| Left behind | none: `docker ps -a --filter label=wdf.dev/verify-run` lists 0 after the run (`containers.left.label_all`) |
| App database | `timescale/timescaledb:2.31.0-pg16`, PostgreSQL 16.15 (`pg.version`), TimescaleDB 2.31.0 (`pg.timescaledb_version`), telemetry off by setting |
| Object store | `chrislusf/seaweedfs:4.51` with `weed server -s3`, `weed admin` and `weed worker -jobType=s3_lifecycle` as three processes of one container |
| Client | `psycopg` 3.3.6 from `uv.lock`, boto3 1.43.119 |

Image digests (`image.timescaledb.digest`, `image.seaweedfs.digest`):

| Image | Digest |
| --- | --- |
| `timescale/timescaledb:2.31.0-pg16` | `sha256:322ae364eca3321cf2a48f1ef31213b87843413b3cf8feeebf54e999a26bb731` |
| `chrislusf/seaweedfs:4.51` | `sha256:f7a49e3f3972ebed5be2ed4befa4a0c68eb7bdddba6e692d42729ceb545134b8` |

The TimescaleDB tag is the newest `-pg16` release on the day of the run (2.31.0, published 2026-10-20), and `latest-pg16` carries the same digest.
Temporal, its Postgres and RabbitMQ are not images of this run.

## Method

- The memory figure is the cgroup working set, `memory.current` less `inactive_file`, which is what `docker stats` reports, sampled once a second from the host cgroup of the container, with the peak per phase.
  Peak anon and peak CPU cores over one second are recorded beside it.
- Postgres runs with the settings that the image's `timescaledb-tune` chose for a 1 GiB cap (`pg.setting.shared_buffers` 256MB, `pg.setting.max_connections` 25, `pg.setting.work_mem` 4MB, `pg.setting.effective_cache_size` 768MB, `pg.setting.max_wal_size` 1GB).
  The data directory is on the container filesystem, not on a volume claim.
- Tables follow section 0.3 of the breakdown: `accepted_job` with its unique key, partial index and generated `manifest_sha256`, and `event` as a hypertable with a one day chunk interval, `unique (event_id, event_time)`, an index on `(job_id, event_time)`, and compression segmented by `job_id`.
- The 14 event rows per job are six job events and four per task for two tasks.
  Their bodies are the golden vectors of `contracts/events/vectors/golden/` with the ids, times, tenant, task and job rewritten per job, 589 bytes on average (`pg.event_body_bytes_avg`).
  Jobs are created six seconds apart across 7 days, so the load fills 8 chunks (`pg.chunks`).
- Phases: idle, 100,000 rows in 100 commits of 1,000, four updates of every row (published, admitted, running, succeeded), 1.4 million events in batches of 5,000 through `unnest` with `on conflict do nothing`, a `compress_chunk` of every chunk ended more than 2 days ago, a `compress_chunk` of the rest, then the batch size sweep.
- The rate sweep inserts 100,000 rows and 280,000 events (20,000 jobs) into fresh scratch tables for each batch size, so the three sizes are comparable.
- Object store phases: the four buckets of `contracts/artifact/layout.yaml` created with one object each, the S5 contract script (base, access, lifecycle with a past dated rule and a one minute detection interval), then 100,000 objects of 1 KiB into `wdf-results` through 16 threads.
  The contract script is the S5 one with its credentials generated for each run, and without the full disk and recover modes, which need a size capped tmpfs.
- The object store was run twice: as in S5, and with `GOMEMLIMIT=512MiB` in the container environment and the contract run skipped.
- The volume size limit is `floor(0.8 x 10,240 / (7 x 4))` = 292 MB, the rule of section 0.3 for a 10 GiB volume claim (`store.volume_size_limit_mb`).

## Measured numbers

### App Postgres with TimescaleDB

| Figure | Value | Name |
| --- | --- | --- |
| Peak working set, idle | 49 MiB | `pg.peak_ws_mib.idle` |
| Peak working set, 100,000 rows | 124 MiB | `pg.peak_ws_mib.rows` |
| Peak working set, row updates | 298 MiB | `pg.peak_ws_mib.rowlife` |
| Peak working set, 1.4 million events | 597 MiB | `pg.peak_ws_mib.events` |
| Peak working set, compression of old chunks | 642 MiB | `pg.peak_ws_mib.compress_old` |
| Peak working set, compression of the rest | 608 MiB | `pg.peak_ws_mib.compress_all` |
| Peak working set of the load, the figure that sizes the node | 642 MiB | `pg.peak_ws_mib.load` |
| Peak anon, peak shared memory | 141 MiB, 275 MiB | `pg.peak_anon_mib.all`, `pg.peak_shmem_mib.all` |
| Peak CPU over one second | 1.31 cores | `pg.peak_cpu_cores.all` |
| OOM kills | 0 | `pg.oom_kill` |
| Time spent at the cap (`memory.events` `max` count) | 36,632 | `pg.memory_max_events` |
| Container start, empty (`docker run` to first query over TCP) | 2.5 s | `pg.start_cold_s` |
| Container restart with the loaded database | 0.8 s | `pg.start_restart_loaded_s` |
| Row load, 100 commits of 1,000 | 6.1 s | `pg.rows_load_s` |
| Row updates, four per row | 6.9 s | `pg.rowlife_s` |
| Event load, 1.4 million rows in batches of 5,000 | 83.4 s, 16,794 rows per second | `pg.events_load_s`, `pg.events_load_rate_s` |
| Compression of 5 old chunks, then 3 more | 25.4 s, then 18.5 s | `pg.compress_old_s`, `pg.compress_rest_s` |
| Compression ratio of the event table | 2.99 | `pg.event_ratio_compressed_all` |

The peak anon is low and the working set high because the 256 MiB of shared buffers are shared memory and the rest is active page cache, which the kernel reclaims at the cap.
The 36,632 reclaim events at the cap are page cache reclaim, and no process was killed.

### Disk per job

Sizes are taken after a `CHECKPOINT`.
`pg.bytes_per_job.<state>` is `pg_total_relation_size('accepted_job')` plus `hypertable_size('event')`, divided by 100,000 jobs.

| State | Bytes per job | Of which event rows | Database | Data directory with WAL | Names |
| --- | --- | --- | --- | --- | --- |
| Empty schema | 1 | 0 | 9 MiB | 42 MiB (16 MiB WAL) | `pg.bytes_per_job.empty`, `pg.database_mib.empty`, `pg.pgdata_mib.empty`, `pg.pg_wal_mib.empty` |
| Uncompressed | 16,252 | 13,874 | 1,559 MiB | 2,584 MiB (1,008 MiB WAL) | `pg.bytes_per_job.uncompressed`, `pg.bytes_per_job.uncompressed.event`, `pg.database_mib.uncompressed`, `pg.pgdata_mib.uncompressed`, `pg.pg_wal_mib.uncompressed` |
| Chunks older than 2 days compressed (5 of 8) | 10,917 | 8,539 | 1,051 MiB | 2,044 MiB (976 MiB WAL) | `pg.bytes_per_job.compressed_old`, `pg.bytes_per_job.compressed_old.event`, `pg.database_mib.compressed_old`, `pg.pgdata_mib.compressed_old`, `pg.pg_wal_mib.compressed_old` |
| Every chunk compressed | 7,001 | 4,624 | 678 MiB | 1,623 MiB (928 MiB WAL) | `pg.bytes_per_job.compressed_all`, `pg.bytes_per_job.compressed_all.event`, `pg.database_mib.compressed_all`, `pg.pgdata_mib.compressed_all`, `pg.pg_wal_mib.compressed_all` |

The accepted row costs 2,377 bytes per job after its four updates, with or without compression (`pg.bytes_per_job.uncompressed.accepted_job`).
The WAL directory sits at its `max_wal_size` of 1 GiB once the load has run, so it is a fixed cost of the volume claim and not a cost per job.

### Insert and ingest rate per batch size

Rows per second into a fresh table, one commit per batch, with `on conflict do nothing`.
Insert is `accepted_job` rows and ingest is `event` rows.

| Batch size | Insert, rows per second | Ingest, events per second |
| --- | --- | --- |
| 1,000 | 18,345 (`pg.insert_rate_rows_s.1000`) | 16,309 (`pg.ingest_rate_events_s.1000`) |
| 5,000 | 19,622 (`pg.insert_rate_rows_s.5000`) | 17,033 (`pg.ingest_rate_events_s.5000`) |
| 20,000 | 19,484 (`pg.insert_rate_rows_s.20000`) | 17,206 (`pg.ingest_rate_events_s.20000`) |

The three sizes are within 6 percent of each other, so the batch size is not a lever on this host and 5,000 rows stays the starting constant.
The rates are single client, loopback, with the rows already built in memory, so they bound what the database accepts and say nothing about the dispatch path.

### Object store with admin and lifecycle worker

| Figure | Value | Name |
| --- | --- | --- |
| Container start to an answering S3 port, to a reachable admin server | 3.0 s, 13.2 s | `store_tuned.start_s3_ready_s`, `store_tuned.start_admin_ready_s` |
| Resident size at idle: server, admin, worker | 116, 108, 102 MiB | `store_tuned.rss_mib.server.idle`, `store_tuned.rss_mib.admin.idle`, `store_tuned.rss_mib.worker.idle` |
| Volumes after the four buckets were each written once | 28 | `store_tuned.volumes.provisioned` |
| Volumes after 100,000 objects of 1 KiB | 35 | `store_tuned.volumes.after_objects` |
| Data on disk after 100,000 objects | 371 MiB | `store_tuned.data_mib.after_objects` |
| Objects stored and listed | 100,000 of 100,000, 100,000 listed | `store_tuned.objects_stored`, `store_tuned.objects_listed` |
| Put rate through 16 threads | 383 per second | `store_tuned.objects_put_rate_s` |
| Peak working set with `GOMEMLIMIT=512MiB`, the figure that sizes the node | 533 MiB | `store_tuned.peak_ws_mib.load` |
| Peak anon, peak CPU over one second | 516 MiB, 1.49 cores | `store_tuned.peak_anon_mib.all`, `store_tuned.peak_cpu_cores.all` |
| OOM kills, time at the cap | 0, 0 | `store_tuned.oom_kill`, `store_tuned.memory_max_events` |
| Server resident size after the objects | 530 MiB | `store_tuned.rss_mib.server.after_objects` |

The default run (S5 settings, no `GOMEMLIMIT`):

| Figure | Value | Name |
| --- | --- | --- |
| Peak working set during the S5 contract run | 225 MiB | `store.peak_ws_mib.contract_lifecycle` |
| Objects stored before the server was killed | 87,809 | `store.objects_stored` |
| Peak working set at the kill | 1,024 MiB, the cap | `store.peak_ws_mib.load` |
| OOM kills | 1 | `store.oom_kill` |
| Volumes after the contract run | 42 | `store.volumes.after_lifecycle` |

The contract run on the default container (`store.contract.base.pass` 14, `store.contract.base.fail` 0; `store.contract.access.pass` 76, `store.contract.access.fail` 4; `store.contract.lifecycle.pass` 5, `store.contract.lifecycle.fail` 0) repeats the S5 result: 14 of 14 base checks, the four known access failures (a role with `Write` can replace its bucket's lifecycle rules, which section 0.2 question 4 records as a known gap), and expiry observed.
The first four buckets allocate 28 volumes of the 292 MB limit, which is the 7 x 4 of the sizing rule, and 100,000 small objects of one bucket take 7 more.
Volumes are not preallocated, so the 28 volumes use 0 MiB at idle (`store_tuned.data_mib.provisioned`).

### Carried, not re-run

| Component | Peak | Limit | Source |
| --- | --- | --- | --- |
| Temporal | 686 MiB | 1,024 MiB | S3b: peak anon with both history caches capped, sized at 1 CPU and 1 GiB |
| Temporal's Postgres | 1,024 MiB, standing in for a peak that S3b did not record | 1,024 MiB | S3b: run at 1 GiB, 3,507 MiB on disk at 100,000 jobs |
| RabbitMQ | 154.6 MiB | 256 MiB | S4: peak 154.6 MiB at a 1 GiB cap, the limit is a decision of this spike |
| `wdf-control` pods on the infra node | 640 MiB (estimate) | 640 MiB (estimate) | an estimate until the dispatch API, the workflow worker and admission replace it |

The control reserve is the dispatch and control API at 256 MiB, the workflow worker at 256 MiB and admission at 128 MiB.

### Images

| Image | Unpacked | Compressed over the wire | Names |
| --- | --- | --- | --- |
| `timescale/timescaledb:2.31.0-pg16` | 1,818 MiB | 590 MiB | `image.timescaledb.unpacked_mib`, `image.timescaledb.compressed_mib` |
| `chrislusf/seaweedfs:4.51` | 511 MiB | 188 MiB | `image.seaweedfs.unpacked_mib`, `image.seaweedfs.compressed_mib` |

Container start times are in the tables above, and the store's include 12 s and 4 s of deliberate waits before the admin server and the worker start: 2.5 s for an empty app database and 0.8 s for a restart of a loaded one, and 3.0 s to an answering S3 port and 13.2 s to a reachable admin server for the store.
The images were already on the host, so pull time is not measured, and the compressed sizes are the figure the CI cost estimate of the breakdown reads for a clean runner.

## Node and quota table

Rules, from the repository: allocatable is the node size less 256 MiB less one sixteenth of it, the quota of `wdf-infra` is allocatable plus 5 percent (`scripts/cluster/namespaces.sh render`, run on a probe copy of the preset for each size), and the daemons take 50 MiB.
A limit of a measured component is its peak plus a quarter in whole 64 MiB steps, and a carried component keeps its source figure.

| Component | Peak (MiB) | Limit (MiB) | Names |
| --- | --- | --- | --- |
| App Postgres with TimescaleDB | 642 | 832 | `node.component.appdb.peak_mib`, `node.component.appdb.limit_mib` |
| Object store, admin and worker | 533 | 704 | `node.component.objectstore.peak_mib`, `node.component.objectstore.limit_mib` |
| Temporal | 686 | 1,024 | `node.component.temporal.peak_mib`, `node.component.temporal.limit_mib` |
| Temporal's Postgres | 1,024 | 1,024 | `node.component.temporaldb.peak_mib`, `node.component.temporaldb.limit_mib` |
| RabbitMQ | 155 | 256 | `node.component.rabbitmq.peak_mib`, `node.component.rabbitmq.limit_mib` |
| Sum of the five `wdf-infra` components | 3,040 | 3,840 | `node.sum_peaks_mib`, `node.sum_limits_mib` |
| `wdf-control` reserve (estimate) | 640 | 640 | `node.component.control_reserve.limit_mib` |
| Sum with the reserve | 3,680 | 4,480 | `node.sum_limits_with_control_mib` |
| Sum of peaks with the reserve plus 20 percent | 4,416 | | `node.sum_peaks_with_control_plus_20pct_mib` |

| Line | `ci` | `dev` | Names |
| --- | --- | --- | --- |
| Infra node now | 3,072 MiB | 4,096 MiB | `node.ci.current.infra_mib`, `node.dev.current.infra_mib` |
| Quota now | 2,756 MiB | 3,764 MiB | `node.ci.current.quota_mib`, `node.dev.current.quota_mib` |
| Passes now | no | no | `node.ci.current.pass`, `node.dev.current.pass` |
| **Proposed infra node** | **5,120 MiB** | **5,120 MiB** | `node.ci.infra_mib`, `node.dev.infra_mib` |
| Allocatable, 5,120 - 256 - 320 | 4,544 MiB | 4,544 MiB | `node.ci.allocatable_mib`, `node.dev.allocatable_mib` |
| Allocatable after the daemons, 4,544 - 50 | 4,494 MiB | 4,494 MiB | `node.ci.allocatable_after_daemons_mib`, `node.dev.allocatable_after_daemons_mib` |
| **Proposed `wdf-infra` quota**, 4,544 x 1.05 rounded up | **4,772 MiB** | **4,772 MiB** | `node.ci.quota_mib`, `node.dev.quota_mib` |
| Limits 3,840 at most the quota | yes | yes | `node.ci.check.limits_within_quota`, `node.dev.check.limits_within_quota` |
| Limits with the reserve, 4,480, at most 4,494 | yes | yes | `node.ci.check.limits_with_control_within_allocatable`, `node.dev.check.limits_with_control_within_allocatable` |
| Peaks with the reserve plus 20 percent, 4,416, at most 4,494 | yes | yes | `node.ci.check.peaks_plus_20pct_within_allocatable`, `node.dev.check.peaks_plus_20pct_within_allocatable` |
| Sum of node limits with the proposed infra node | 10,880 MiB | 11,264 MiB | `node.ci.sum_of_node_limits_mib`, `node.dev.sum_of_node_limits_mib` |
| `ci` sum at most 15,984 MiB | yes | not a line for `dev` | `node.ci.check.within_runner_mem_total` |

The quota line of the pass line is the sum of the five limits (3,840) against 4,772.
The stricter line, which also counts the control pods that share the infra node and are held by the scheduler because requests equal limits, passes with 14 MiB to spare (4,480 against 4,494).
The next smaller size in steps of 128 MiB, 4,992 MiB, allocates 4,424 MiB and 4,374 MiB after the daemons, and fails that line by 106 MiB.
The smallest numbers that pass are therefore the infra node of 5,120 MiB and the quota of 4,772 MiB.
Because the control reserve is an estimate, the dispatch, workflow and admission lanes owe this table a check when their real figures replace it, and the 14 MiB margin means a reserve above 654 MiB needs the next step.
CPU is not part of the pass line: the peak CPU of the app database during load is 1.31 cores and of the object store 1.49 cores, over one second, so R.1 should check the infra node CPU count against the sum of the requests that D.1 and O.1 choose.

## Volume claim figures

| Claim | Figure | Basis | Names |
| --- | --- | --- | --- |
| App Postgres | 4,096 MiB | the uncompressed data directory with its WAL after 100,000 jobs (2,584 MiB) plus a quarter, rounded up to 1 GiB, covers the 2 days before compression starts | `pvc.appdb_mib`, `pg.pgdata_mib.uncompressed` |
| Temporal's Postgres | 5,120 MiB | S3b, 3,507 MiB at 100,000 jobs with a quarter margin | `pvc.temporaldb_mib` |
| Object store | 10,240 MiB | the figure the run used, with a volume size limit of 292 MB by the rule of section 0.3, so that 28 volumes fit within 80 percent of it | `pvc.store_mib`, `pvc.store.volume_size_limit_mb`, `pvc.store.volume_size_limit_matches_run` |

The object store claim is not derived from a load, because the epic gives no bytes per job for results and checkpoints.
The 100,000 objects of 1 KiB took 371 MiB, which is 3.8 KiB each including the volume index, so small objects cost about three times their size.
The app Postgres claim holds the standard load only and not seven days of the event history of a larger load.
Whether the default storage class enforces a claim size is not measured here, and D.1 records it.

## What the numbers change

1. R.1 sets the infra node of `ci` and `dev` to 5,120 MiB.
   The `ci` total of node limits rises by 2,048 MiB to 10,880 MiB and the `dev` total by 1,024 MiB to 11,264 MiB, both within the runner's 15,984 MiB for `ci`.
   `soak` is not measured here, and its infra node of 6,144 MiB is above the 5,120 MiB figure.
2. The object store module sets `GOMEMLIMIT` for its server, with the limit of 704 MiB for the pod whose three processes together peaked at 533 MiB under that setting.
   The setting caps the server heap, so a larger object count may push the working set against it and slow the server with garbage collection, and that was not measured.
3. The app Postgres module has the limit of 832 MiB, with `shared_buffers` of 256MB as the image tuned it for a 1 GiB cap and a peak working set of 642 MiB.
   The module should set `max_connections` and `shared_buffers` explicitly, since the tuning reads the cap at the first start and the limit differs from the cap used here.
4. The measured rates feed the batch constant of E.2 (5,000 rows) and the report of the bench of Q.2, and no test holds them.
5. The cost section of the breakdown reads 590 MiB and 188 MiB of new compressed images for a clean runner, and a container start of 2.5 s for the app database and 13.2 s for the store with its worker.

## Caveats and what was not measured

- One host, one run of the full sequence for the table above, after earlier runs that exposed harness bugs and the object store kill.
  The Postgres peak varied between 609 and 642 MiB across two complete loads, so the peak has a spread of about 5 percent.
- The cap of 1 GiB is the T1 rule, so a peak that would exceed it is hidden by reclaim for Postgres and shows as a kill for the store.
  The Postgres working set of 642 MiB is below the cap with 36,632 reclaim events, which means the true unconstrained cache use is higher and the limit of 832 MiB leaves the kernel reclaiming page cache in the same way.
- The store peak of 533 MiB is pinned by `GOMEMLIMIT` at 512 MiB, so it is the figure under that setting and not the unconstrained need.
- The data directories are on the container filesystem and not a volume claim, with the fsync and write behaviour of the host disk, which is left to D.1.
- Event bodies are the golden vectors rewritten per job, so compression of real events with more variable data may differ, and the mix of 14 event types is a stand-in with the right byte size.
- No query load ran during ingest, no replication, no `pg_dump` and no restart under write load.
- The `Days` lifecycle rule on aged objects, the abort of incomplete multipart uploads and the full disk behaviour are not run here (the full disk modes need a size capped tmpfs, which the container helper does not offer).
- RabbitMQ, Temporal and its Postgres were not re-run, and the Temporal Postgres peak is its limit.
- The `wdf-control` reserve is an estimate.
- Host `MemAvailable` was 14 GiB, far above the 1 GiB floor of the earlier spikes, so no watchdog was needed, and CPU contention from the 14 other containers was not controlled.
