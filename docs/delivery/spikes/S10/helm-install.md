# Spike: the official Temporal chart on the `ci` preset (S10)

Run on 2026-11-11.
Owner: the [workflow engine epic](https://github.com/AndoNorth/wasm-dag-forge/issues/6), lane S, filed as [spike issue 316](https://github.com/AndoNorth/wasm-dag-forge/issues/316).
Spec: the S10 issue of [the workflow engine breakdown](../../epics/08-workflow-engine.md#4-issues), and decisions 18 and 20 and default D22 there.
Method: [docs/delivery/method.md](../../method.md).
Throwaway code: [code/](code/), run as `nix develop -c docs/delivery/spikes/S10/code/run.sh [out-dir]`, which takes the cluster lock, brings up the `ci` preset, installs the chart, runs the load and destroys the cluster.
Every measured value below is a `figure <name> <value>` line of that run, and the name is given in backticks beside it.

## Result

Diagram: [helm-install.drawio.svg](helm-install.drawio.svg), the Temporal pods on the `ci` infra node with their limits and measured peaks beside `temporaldb`.

**Pass.**
All four items of the pass line hold, so decision 20's one-pod fallback is not needed and M.1 deploys the chart with the four services.

- (1) Frontend, history, matching and worker ran at one replica each, with no Elasticsearch, no web UI and no admin tools pod, and the sum of their limits is 1,024 MiB and 1,000m (`limit_memory_sum_mib`, `limit_cpu_sum_m`).
- (2) 3,000 of 3,000 two-task stand-in jobs finished, 0 failed, with the host workers through a port-forward bound to 127.0.0.1 (`jobs_finished`, `jobs_failed`).
- (3) The history service's mutable state cache series reached 1,000 36 s into the load and stayed at exactly 1,000 for the remaining 34 samples, with no sample above it (`cache_mutablestate_max`, `cache_first_at_max` against `load_start`, `cache_samples_at_max`).
- (4) The `max` counter of `memory.events` stayed 0 in all four pods, `oom_kill` stayed 0 and no container restarted (`memory_events_max_*`, `oom_kill_*`, `restarts_*`).

The script's verdict gates on all four, on one ready replica per service (`replicas_*`) and on no other pod of the release (`other_release_pods`), and printed `pass` (`verdict`).

The cache series, as the history service exports it on port 9090, is:

```text
cache_usage{cache_type="mutablestate",service_name="history",type="history"}
```

Its sibling for the events cache is `cache_usage{cache_type="events",service_name="history",type="history"}` (`cache_series`).
M.1 and row 12 of the exit test read the first one by this name and these labels.

## Question

Does the official chart `temporal` 1.8.0 (server 1.32.2), with Postgres persistence and SQL visibility on `temporaldb`, one replica per service, 512 shards and the two dynamic-config keys of decision 18, fit Temporal's 1,024 MiB and 1 CPU on the `ci` infra node, and do the keys reach the history service?

## Environment

| Item | Value |
| --- | --- |
| Host | WSL2, Linux 6.6.87.2, 20,002 MiB `MemTotal` (`host_mem_total_mib`), 16,416 MiB `MemAvailable` before the run (`host_mem_available_mib`), Docker 29.1.3 (`docker_version`), containers of other work running and untouched |
| Cluster | the `ci` preset through `scripts/up.sh`, with the infra node at 3 CPUs and 5,120 MiB after the D22 revisit, and every module of the `ci` profile through `scripts/tilt/run.sh ci` |
| Chart | `temporal` 1.8.0 from `https://go.temporal.io/helm-charts`, `appVersion` 1.32.2, tarball `sha256:e65b454d620129f3d7da2c16611a7f754e2d89b20b7a135dc5f0916a062f3a9e`, the sha256 of the downloaded tarball, which matches the `digest` of the repository index (`chart_digest`) |
| Server image | `temporalio/server:1.32.2@sha256:7bf69188a6232fea14869b9cef3d94aa82a1601d99517f7e86c71f22da4e0670`, the digest the running history pod reports (`server_image`) |
| Admin tools image | `temporalio/admin-tools:1.32.2@sha256:4a95866efba29d7e17ab012df5232795e8b0ad0f7d5f9d1ec4df321f7792a754`, used only by the schema job |
| Values | [code/values.yaml](code/values.yaml), rendered with `helm template --skip-tests` and `schema.useHelmHooks: false`, the schema job applied first and the server manifests after it completed |
| SDK | `temporalio` 1.35.0 from `uv.lock`, Python 3.13.15, `uv.lock` unchanged |
| Left behind | nothing: the run ended with `scripts/nuke.sh`, after which `kind get clusters` printed `No kind clusters found.` and no process held the cluster lock |

The values follow the issue: the stores `default` and `visibility`, each `sql` with `pluginName: postgres12`, the databases `temporal` and `temporal_visibility` on `temporaldb.wdf-infra.svc:5432`, the password from the `temporaldb` Secret (key `POSTGRES_PASSWORD`), `createDatabase: false` and `manageSchema: true`, `numHistoryShards: 512`, `web.enabled: false`, `server.config.namespaces.create: false`, and no top-level `cassandra`, `elasticsearch`, `mysql`, `postgresql`, `prometheus` or `grafana` key.
Every pod carries the infra pool's node selector and toleration, and requests equal limits.

## Load

The S3 method: each job is one workflow with two tasks, compile then simulate, each preceded by a `place` local activity and running 50 ms of simulated work on a pool queue, with at most 100 workflows in flight, in [code/load.py](code/load.py).
One workflow worker and one pool worker ran on the host in the load process, against the frontend through `kubectl port-forward --address 127.0.0.1`.
The namespace `s10` was registered by the load script with a retention of 1 day, because the chart's namespace job is off.
A sampler read `/proc/uptime`, `memory.current` and `cpu.stat` in each Temporal container and the history service's `/metrics` through a second port-forward, about every 5 s.

| Figure | Value |
| --- | --- |
| Jobs | 3,000 started, 3,000 finished, 0 failed (`jobs_started`, `jobs_finished`, `jobs_failed`) |
| Duration | 216.9 s (`load_seconds`) |
| Throughput | 13.8 jobs per second (`jobs_per_second`) |

## Per-service limits and peaks

The memory peak is the container's `memory.peak`, its high-water mark since the container started, read after the load (`memory_peak_mib_*`).
The CPU rates use the uptime clock read in the same `exec` as `cpu.stat`: the peak is the highest rate between two samples (`cpu_peak_m_*`), and the mean is over the 224 s of samples (`cpu_mean_m_*`, `sample_seconds_*`).
A throttled period is a 100 ms CFS period in which the container hit its quota, out of about 2,240 periods in the samples (`cpu_throttled_periods_*`).

| Service | Memory limit | Memory peak | CPU limit | CPU peak | CPU mean | Throttled periods | `max` before and after | Restarts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| frontend | 192 MiB | 61 MiB | 200m | 140m | 114m | 73 | 0, 0 | 0 |
| history | 576 MiB | 343 MiB | 450m | 401m | 372m | 463 | 0, 0 | 0 |
| matching | 128 MiB | 63 MiB | 200m | 132m | 116m | 113 | 0, 0 | 0 |
| worker | 128 MiB | 35 MiB | 150m | 11m | 7m | 0 | 0, 0 | 0 |
| Sum | 1,024 MiB | 502 MiB | 1,000m | | 609m | | | |

The memory sum adds peaks reached at different times, so it bounds the joint peak from above.
The `wdf-infra` quota with Temporal running used 2,990m of 3,045m CPU limits and 3,632 MiB of 4,772 MiB memory limits (`quota_used`, `quota_hard`).

## Do the keys reach the history service

Yes.
The file mounted in the history pod at `/etc/temporal/dynamic_config/dynamic_config.yaml` holds both keys, `history.hostLevelCacheMaxSize: 1000` and `history.eventsCacheMaxSizeBytes: 32768`, and the rendered server config points `dynamicConfigClient.filepath` at it with the chart's `pollInterval` of 10 s.
The mutable state cache rose through 4, 337, 488, 666, 821 and 988 in the first six samples, reached 1,000 and then held at exactly 1,000 for 34 samples while about 2,500 more jobs finished.
Without the cap, the S3b finding is that nothing evicts below the default of 128,000 entries, and the 200-job shakedown ended at 404, about 2 entries per job, so the series would have kept climbing to about 6,000.

## What the numbers change

- Decision 20's fallback is not taken: M.1 deploys chart `temporal` 1.8.0 with one replica per service.
- The starting split holds unchanged as the limits M.1 pins: history 576 MiB and 450m, frontend 192 MiB and 200m, matching 128 MiB and 200m, worker 128 MiB and 150m.
- Memory has margin at this load: the four per-pod peaks sum to 502 MiB of 1,024 MiB, and history peaked at 60 percent of its limit.
- CPU is the tight resource: history ran at a mean of 83 percent of its limit and was throttled in about 21 percent of periods, while the quota has 55m to spare.
- The exit test and the observability epic read `cache_usage{cache_type="mutablestate",service_name="history",type="history"}` on port 9090 of the history pod.
- Admin tools stay off in the module, because the schema job is the only user of that image and a resident pod would take quota the infra node does not have.

## Caveats and what was not measured

- Throughput of 13.8 jobs per second is below the 35 to 50 of S3's single process with direct access, and below S3's sustained threshold of 15, which is not part of this pass line.
  The load ran through one `kubectl port-forward` and history was throttled, and the run does not separate the two.
- The load is 3,000 jobs, not the 100,000 of S3b, so the memory peaks say nothing about long-run growth beyond the cap holding.
- The figures are from the third run.
  A 200-job shakedown and a first 3,000-job run with the same values, and an earlier sampler that timed samples in whole seconds, also passed, the second with 3,000 of 3,000 jobs, the cache at 1,000 and no `max` rise.
- No control run without the keys was made, so the uncapped growth is S3b's figure and not one measured on the chart.
- CPU figures come from 5 s samples through `kubectl exec`, not a metrics server.
- Not tested: a rolling restart of a service, a change of a key at runtime, and the namespace, search attributes and Schedule of the bootstrap module.
