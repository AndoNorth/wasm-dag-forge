# Spike: Temporal with the Python SDK (S3 and S3b)

Two linked spikes, both run on 2026-10-27.
The first (S3) tested the workflow engine design at container level against six thresholds.
The second (S3b) followed up on the one threshold that failed, memory growth, and sized Temporal for 100,000 jobs.
Owner: the [workflow engine epic](https://github.com/AndoNorth/wasm-dag-forge/issues/6).
The characterization epic ([system characterization and soak tests](https://github.com/AndoNorth/wasm-dag-forge/issues/16)) owns the follow-up sizing runs.

## Question

- S3: does the workflow design work on current stable Temporal with the Python SDK?
  The design is a local activity `place`, a pool-queue activity, heartbeat cancel, server fencing of zombie attempts, pinned worker versioning on the workflow queue only, and CloudEvent JSON payloads.
- S3b: what retains memory per finished workflow, and what does Temporal need at 100,000 jobs?

## Environment and versions

| Item | Value |
| --- | --- |
| Host | WSL2, Linux 6.6.87.2, 7.8 GiB RAM, 6 CPUs (S3b), Docker 29.1.3 |
| Temporal server | `temporalio/server:1.32.1`, one process running all four services |
| Postgres | `postgres:16` (16.14), both stores, SQL visibility, no Elasticsearch |
| Python SDK | `temporalio 1.34.0`, Python 3.12.3 |
| Shards | 4 in S3, 512 (Helm chart default) in S3b unless stated |
| Workers | host processes outside Docker: one workflow worker, one activity worker, one driver |

## Method

- Each job is one workflow with two tasks, compile then simulate.
  Each task is preceded by a `place` local activity and runs 50 ms of simulated work.
- A semaphore holds at most 100 workflows in flight.
- S3 ran five runs of 10,000 to 20,000 jobs and measured memory with `docker stats` and the cgroup `memory.stat`.
- S3b ran 20,000-job lever runs on a clean database each, then 100,000-job runs with Temporal limited to 1 CPU and Postgres to 1 GiB.
- Slopes are least-squares fits against finished workflows.
  Live heap is `HeapAlloc` after a forced GC from the pprof endpoint, and anon is the cgroup `anon` figure.

## Results: S3 thresholds

| Threshold | Result | Verdict |
| --- | --- | --- |
| v1 workflows finish on v1 | open v1 workflow finished with `code=v1` while v2 was current | pass |
| New workflows run on v2 | 5 of 5 ran `code=v2` | pass |
| Activity queue unaffected by versioning | unversioned activity worker served v1 and v2 workflows | pass |
| Cancel reaches the activity within 0.8 x heartbeat timeout | max 3.94 s at 5 s, 7.27 s at 10 s, 19.58 s at 30 s | pass, thin at 5 s |
| Sustained at least 15 completed jobs/s | 39 to 50 jobs/s overall, lowest 15 s window 35.6, 80,000 jobs | pass |
| Memory growth under 300 MiB over 20,000 jobs | +1,216 MiB untuned, +1,000 MiB with `GOMEMLIMIT` 400 MiB | **fail** |
| No workflow task failures | 0 `WorkflowTaskFailed` in 30,000 scanned histories, 2 `WorkflowTaskTimedOut` | pass |
| Visibility lag | max 5 jobs behind, catch-up at most 1.1 s, no growth over 60,000 workflows | recorded, flat |

## Results: S3b memory levers at 20,000 jobs

| Change from default | Live heap slope (MiB per 1,000 jobs) | Anon slope (MiB per 1,000 jobs) |
| --- | --- | --- |
| Default (512 shards) | 32.0 and 31.1 | 62.7 and 60.0 |
| Retention 60 s | 32.0 and 30.7 | 61.5 and 61.2 |
| `history.hostLevelCacheMaxSize` 1000 | 10.3 | 22.4 |
| `history.hostLevelCacheMaxSize` 1,000,000 | 31.8 | 62.0 |
| `history.eventsCacheMaxSizeBytes` 32 KiB | 26.3 | 53.0 |
| Both caches small | 4.9 | 11.2 |
| `GOMEMLIMIT=1200MiB` only | 29.6 | 34.3 |
| 4 shards | 19.7 and 19.6 | 39.5 and 40.1 |
| 64 shards | 30.8 | 61.6 |

## Results: S3b at 100,000 jobs

All rows: 512 shards, retention 1 day, 100 jobs in flight, Temporal at 1 CPU, Postgres at 1 GiB.

| Configuration | Outcome | Peak Temporal anon | Throughput |
| --- | --- | --- | --- |
| Default, 2 GiB | wedged at 24,125 jobs | 2,034 MiB at the stall | 29.8 to 32.8 jobs/s while healthy |
| Default, 3 GiB | wedged at 39,412 jobs | 3,055 MiB at the stall | not reported |
| History cache cap only, 2 GiB | wedged at 70,957 jobs | 2,030 MiB at the stall | not reported |
| Both caps, 2 GiB | 100,000 of 100,000, 0 failed jobs | 678 MiB | 32.4 jobs/s |
| Both caps, 1 GiB | 99,998 of 100,000, 2 failed jobs | 686 MiB | 34.1 jobs/s |

- Best configuration: live heap flat at 235 to 273 MiB from 10,000 jobs on, slope 0.02 MiB per 1,000 jobs from 10,000 to 100,000 (2 GiB run).
- Latency of the best 2 GiB run: p50 2.41 s, p99 7.07 s.
- Postgres grew linearly to 3,507 MiB (about 35 KiB per job, 71 percent in the visibility database) and throughput drifted down about 13 percent over the run.
- Full history scan of the 1 GiB run: 0 `WorkflowTaskFailed`, 69 `WorkflowTaskTimedOut` (0.069 percent), all retried and completed.

## Findings that change the design

1. The workflow engine design holds on Temporal 1.32.1 with Python SDK 1.34.0.
   The local-activity placement answer is recorded as a marker, so replay does not call it again.
2. Memory growth is the history service's mutable-state cache plus the per-shard events cache.
   The default entry cap of 128,000 is above what a 100,000-job run produces, so nothing evicts.
   Retention does not drive it.
3. Two dynamic-config keys remove the growth: `history.hostLevelCacheMaxSize: 1000` and `history.eventsCacheMaxSizeBytes: 32768`.
   Capping only one still wedged a 2 GiB container.
4. Sizing for 100,000 jobs: Temporal at 1 CPU and 1 GiB (2 GiB as margin if payloads or the in-flight cap grow), Postgres at 1 GiB memory and at least 4 GiB disk.
   Without the caps, more than 3 GiB is needed (an estimate of 4 to 5 GiB, not measured).
5. A memory-starved Temporal wedges instead of being OOM killed (`OOMKilled` false, `oom_kill` 0 in every run).
   Detection needs a throughput stall check plus the cgroup `memory.events` `max` counter.
6. The Python SDK has worker-side versioning support but no high-level client call to set the current version.
   The spike used the raw gRPC `set_worker_deployment_current_version`, so the rollout needs a small helper or the `temporal worker deployment` CLI.
7. Cancel latency ceiling is the 0.8 x heartbeat timeout by construction, and the 5 s timeout leaves only 0.06 s of slack.
   A zombie attempt's next heartbeat is cancelled with `not_found=true`.
8. Shard count cannot change on an existing cluster, so the chart default of 512 is fixed unless a study deliberately uses 4.
9. Activities need a retry policy: 2 of 100,000 jobs failed on a heartbeat timeout after lost completion RPCs at the 1 CPU limit.

## Caveats and what was not measured

- The cache keys were exercised through a config file on one process, not through the Helm chart's dynamic-config values.
- Services ran in one process, so per-service pod limits are inferred from the heap profile (history 76 to 100 percent).
- The 4 GiB default-configuration step was not run because host memory would have fallen under the abort threshold.
- The 2 GiB best run has no full history scan and no server log.
- In-run visibility lag samples before the follow-up runs are invalid, and the end-of-run catch-up of 1.0 s is the reliable figure.
- Temporal CPU use was not recorded.
- Task work is a 50 ms sleep and payloads are about 400 bytes, so retained bytes per workflow will grow with real payloads.
- Not tested: versioned activity workers on a separate deployment, drain of an old version, replay tests, `history.cacheTTL` as an alternative to the entry cap, and a split-service deployment.
