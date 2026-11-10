# Epic #6: Workflow engine (Temporal) - breakdown

Status: draft
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/6
Architecture note: docs/architecture/12-dag-and-workflow.drawio
Verifier: none, per decision 1.
Depends on (closed epics): Dev environment (#1), Fixed-resource cluster (#2), Job event envelope (#4), DAG configuration schema and validator (#5), Durable state and artifact storage (#3), Artifact passing (#12), Workload toolchain (#11)

Evidence marks: `[read]` means read on `main` at `a1a62d4` on 2026-11-10 while preparing this spec, and nothing was executed beyond read-only commands.
The epic issue body was read on 2026-11-10.
Method: [docs/delivery/method.md](../method.md).
Model: [docs/delivery/epics/07-workload-toolchain.md](07-workload-toolchain.md) and [06-artifact-passing.md](06-artifact-passing.md), whose structure this spec follows.
Tiers: T0 is host Python with no runtime, T1 is containers started through `wdf_itest` (the Temporal dev server of the pinned CLI image, Postgres and SeaweedFS, at most two containers across the host), and T2 is the cluster from `just up` on preset `ci`.
A test that needs a container carries the `integration` marker and runs in `just itest` and in every lane's `just ci`.
Commands are written without the `nix develop -c uv run --frozen` prefix that every Python command takes, and T2 commands run under `flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 ...` as the cluster epic says.

## 0. Decisions

Section 0.1 lists what is already decided and carried, 0.2 the questions, all answered, 0.3 the defaults that the lanes build on, and 0.4 the proposals that lane 0 and the issues pin.
A change to a default or a proposal changes only the issues named in its last column.

### 0.1 Recorded decisions

Everything in the epic body stands except where merged work has overtaken it, which this table and section 1 list.

| # | Decision | Source | Applied in |
| --- | --- | --- | --- |
| 1 | No verifier skill, no feature files and no verify recipes for this epic, and the `verify-execution` row of the method is dropped for #6. Every issue names its tests and the exit test is a command checklist | the standing rule applied by the envelope, validator, state, artifacts and toolchain epics (`05-state-storage.md:27`, `06-artifact-passing.md:36`, `07-workload-toolchain.md:45`) | sections 3, 4, 6 |
| 2 | Python models are the source of anything with a schema, and the exported JSON Schemas are checked in with a drift test | envelope epic decision 5, carried by every later epic | 0.1, 0.4 |
| 3 | Runtime libraries read contract files through package data and never by repository path, and every package has a wheel test | state epic decision 11, carried | 0.2, every package issue |
| 4 | The workflow input is the accepted event plus the `job.params` block, which admission reads from the row and passes as a second start argument, and the workflow never reads Postgres to decide lifecycle. This replaces the #4 Interfaces row "the workflow input is the accepted event" | envelope question 3 (`03-envelope.md:67`), `wdf_events.models.JobParams`, the stand-in `StandInJob.run(accepted, params)` [read] | W.1, A.1 |
| 5 | A partial run ends in status `partial` with the event `dev.wdf.job.partial.v1`, whose data lists the tasks that did not run to completion. This answers the epic's open question 3 | envelope question 2 (`03-envelope.md:66`), `contracts/status/` status v1 | P.1, W.3 |
| 6 | Job and task events leave through one batched local activity behind one emit point, with deterministic ids and the time of the fact, and the workflow projects `running`, `paused` and the terminal statuses through the guarded `transition` under role `wdf_workflow`. Temporal has its own plain Postgres, delivered as `deploy/temporaldb/`. This answers open questions 10 and 11 | `05-state-storage.md:159,176`, `contracts/state/README.md:159`, `deploy/temporaldb/module.yaml` [read] | A.1, M.1 |
| 7 | A manual pause cancels a running checkpoint-capable simulate task, the worker takes a final checkpoint and exits with class `operator_interrupt`, which consumes no budget and advances the resume counter, any other running task runs to completion, and the job projects `paused` only when nothing runs. The server-side activity pause is not used. This answers open question 2 | #24 body "Manual pause of a running task (decided, with #6, #10 and #18)", #10 and #18 bodies [read] | W.4, 0.1 |
| 8 | Admission learns of a pause from the job row that the workflow projects, and a granted resume is signalled to the paused workflow by admission. This answers open question 1 | #8 and #24 Interfaces rows, #18 body [read] | W.5, 0.1 |
| 9 | The compile cache key is `wdf_keys.compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version, entry)`, five inputs, computed nowhere else, with the two toolchain inputs from `wdf_runtime.pins`. This replaces the "cache key" wording of the epic body | events v3 (`contracts/events/README.md`, Keys), toolchain decision 5 [read] | A.2 |
| 10 | The workflow looks up the cache before placing a compile task, links a hit with `cache.use`, records the entry with `cache.record` after the commit of the compile attempt, refuses to record an entry whose `build-info.json` carries another `cache_key` than the one it computed, treats a lookup error as an error and never a miss, and treats `ARTIFACT_LOST` of a cache-sourced component as an invalidation and a rebuild. The compile worker writes no cache entry, which supersedes the #10 Interfaces row for #12 | artifacts decision 18 and rules row for #6 (`06-artifact-passing.md:53,226`), toolchain decision 6 and rules row for #6 (`07-workload-toolchain.md:35,109`) [read] | A.2, W.1 |
| 11 | The workflow commits only the accepted attempt with a client of the `workflow` store role, retries the attempt when the commit raises `OUTPUT_UPLOAD_FAILED`, builds the aggregate index in an activity before the aggregate task from the recorded results in task id order with `build_index` and `put_index`, commits the index reference, and chooses the previous checkpoint after `CHECKPOINT_CORRUPT` | artifacts rules row for #6 (`06-artifact-passing.md:226`), artifacts D7 (`:91`) [read] | A.2, W.3, W.5 |
| 12 | The workflow passes the bound `params` of each task to the worker in the activity payload that this epic pins, because the task request carries none, and the worker writes them as `/in/input.json` | toolchain 0.4 Input file row (`07-workload-toolchain.md:86`), `TaskRequestData` [read] | 0.1 |
| 13 | The workflow maps every class of `contracts/workload/exit-classes.yaml` and every code of `contracts/artifact/failures.yaml` into its failure class table and never retries a deterministic class. Aggregate waits for every input unless the task sets `allow_partial` | toolchain rules row for #6, artifacts rules row for #6 [read] | 0.1, P.2 |
| 14 | No submit-time resource check: the runtime library refuses a task whose guest cap is below `GUEST_MIN_PAGES` (209 pages) as a deterministic `guest_memory_cap`, which the workflow does not retry. The first run of a component on a pod is a separate warm-up of about 30 s at 250m before the task's guest runs, so the static pool table gives the simulate pool a heartbeat timeout and a start-to-close margin above it | toolchain decisions 20, 21 and 24 (`07-workload-toolchain.md:49,50,53`) [read] | 0.1 |
| 15 | `paced_sim`, the cooperative stop marker, the checkpoint format and the kill-and-resume test belong to the checkpoint epic's pass (#18), so this epic tests pause and resume with fake checkpoint references | toolchain decision 13 [read] | W.5 |
| 16 | The reaper's Temporal Schedule worker is deployed with this epic's worker service, and a test holds the reaper's Schedule id and task queue (`wdf-artifact-reaper`) equal to the naming contract | artifacts closure "What moved" (`06-artifact-passing.md:513,516`), `packages/artifactreaper/src/wdf_artifactreaper/schedule.py:35-39` [read] | 0.1, D.1 |
| 17 | Real stages call `run_stage_contract`, so the real workflow passes `run_stage_contract("workflow", ...)`, supplies the event sequence after a resume and calls `stage_span` itself. The status move matrix may be extended only by a contract-change PR | envelope closure follow-ups (`03-envelope.md:351-356`) [read] | W.1, V.1 |
| 18 | The two dynamic-config keys `history.hostLevelCacheMaxSize: 1000` and `history.eventsCacheMaxSizeBytes: 32768`, Temporal at 1 CPU and 1 GiB, its Postgres at 1 GiB and at least 4 GiB of disk, and 512 shards fixed at install | the epic body, S3b (`docs/delivery/spikes/temporal.md`) [read] | M.1, S10 |
| 19 | The pause reason (`operator`, `retries-exhausted`, `resume-compile-failed`) and the tasks that never ran are fields of the status query, and no contract changes: `job.paused` keeps no data, the job row gets no reason column, and `task.skipped` keeps only `compile-cache-hit`. The event history still holds the cause of every pause through the preceding `job.pause.requested` or `task.retries.exhausted` event, the `job.failed` reason text and the `skipped_tasks` of `job.partial` (question Q1, answer (a)) | answer of 2026-11-10 | 0.4, A.1, W.4, W.5 |
| 20 | If spike S10 shows that the official chart's four service pods do not fit Temporal's 1,024 MiB on the infra node with the two cache keys of decision 18 active, one pod runs every service in one `temporal-server` process from the pinned server image, the shape S3b measured, with the same keys and limits. The infra node stays at 5,120 MiB, and a split-service install stays possible on a larger preset (question Q2, answer (a)) | answer of 2026-11-10 | S10, M.1, M.2 |
| 21 | The cluster (T2) proves the Temporal module, the bootstrap and the deployed workflow worker, driven by the harness with fake pool workers on the host through a `kubectl port-forward` bound to 127.0.0.1, because T1 holds at most two containers and the cluster is the only place where real activities, Postgres, the store and Temporal meet in this epic (question Q3, answer (a)) | answer of 2026-11-10 | D.2, V.2, V.3, section 7 |
| 22 | `oom_killed` of the simulate pool consumes the infrastructure budget, because a kill while the guest is below its `-W max-memory-size` cap is a platform sizing fault, and `compile_oom` and `aggregate_oom` consume the guest budget with #9's single escalation when a larger memory class exists (question Q4, answer (b)) | answer of 2026-11-10 | 0.4, P.2 |
| 23 | The job pauses when a task whose plan `checkpoint` flag is set exhausts a budget, and a resume re-runs every task that has not succeeded (paused and failed alike) with fresh budgets and the next resume counter, from the last good checkpoint where one exists (question Q5, answer (a)) | answer of 2026-11-10 | P.1, W.5 |

### 0.2 Questions

All questions are answered: the recommended option of each of Q1 to Q5 of the draft was taken on 2026-11-10, recorded as decisions 19 to 23 in section 0.1.

### 0.3 Defaults the lanes build on

These carry the remaining open questions of the epic and the choices found while reconciling it.
Numbers marked as estimates are fixed by #16.

| # | Topic | Default | Reason, and the alternative | Changes |
| --- | --- | --- | --- | --- |
| D1 | Plan loading (open question 9) | An activity `load_plan` reads the manifest of the accepted event with the `workflow` store role, checks its hash, reads it with `wdf_plan.read_plan` and binds the params with `wdf_plan.bind`, and the bound plan (at most 256 KiB of canonical bytes, `max_plan_bytes`) is recorded in history. A resume with a patched manifest loads it the same way | Decision 4 keeps the plan out of the start input, and the manifest is needed for a patched resume either way | A.1, W.1 |
| D2 | How the checkpoint reference reaches the workflow (open question 8) | A failure the worker reports carries the latest checkpoint reference in its failure details, and a heartbeat timeout is read from the last heartbeat details, so a lost worker still hands over its last reference. The workflow holds the last good reference per task | A dead worker cannot report, so heartbeat details are required, and failure details avoid a race with the last heartbeat | 0.1, W.2, W.5 |
| D3 | Infrastructure budget | 10 attempts per task, reset at each resume, with backoff 10 s doubling to 10 min (estimates). Exhausting it follows the same rule as the guest budget (D9) | A weekly rollout and a node loss cost one each, and a permanently broken pool stops within about an hour | 0.1, P.2 |
| D4 | Timeouts | The guest's own run timeout (`timeout`, `compile_timeout`, `aggregate_timeout`) consumes the guest budget. A Temporal start-to-close or heartbeat timeout consumes the infrastructure budget | The run timeout is the author's declared limit, and a Temporal timeout means the worker did not report | 0.1, P.2 |
| D5 | Schedule-to-start (open question 6) | The first schedule-to-start timeout is 1,800 s, doubling at each re-schedule to a ceiling of 12 h, with no count cap, an alert metric at each timeout and a new `place` call before scheduling again (estimates). Over the 7-day maximum job duration this bounds a task to about 20 re-schedules and about 120 history events | The epic fixes that a task is never dropped, and a re-schedule moves the task to the tail of its FIFO queue, so short timeouts would starve it under a deep backlog | 0.1, P.2, W.2 |
| D6 | Versioning behaviour (open question 7) | Every job workflow is `PINNED` to the build that started it, the workflow queue is the only versioned queue, the control and pool activity queues are unversioned, and the pinned-bug override is the procedure that S9 settles | S3 showed an unversioned activity worker serving both builds, and auto-upgrade of short jobs would need every change to be replay-safe | V.2, S9 |
| D7 | Continue-as-new and segments | None in this pass. Segments of 1 to 6 h and continue-as-new at a quiescent point are built with #18's pass, which takes over the workflow files it changes through its own lanes, and the epic's "continue-as-new readiness spike" moves there | The epic body says no continue-as-new in v1, the plan limits bound history, and only the 30-day segmented job needs it | none |
| D8 | Drain | After a budget is exhausted, every task that does not depend on the exhausted task keeps running, including tasks that become ready, and the job pauses or ends when nothing can run. After a manual pause no new task is placed and running ones finish (decision 7) | The epic's "independent branches drain" for exhaustion, and #24's "places no new task" for an operator pause | P.1, W.4 |
| D9 | Pause or fail | The rule keys on the plan's `checkpoint` flag of the task that exhausted a budget, not on the job class and not on whether a checkpoint was written. A checkpoint-capable task that wrote none resumes from its start. A job with a paused chain and a failed chain pauses, and the status query lists both | #18's open question "keyed on has written a checkpoint, or on the long job class", answered by the plan flag, which the workflow already reads | P.1, W.5 |
| D10 | Temporal Web UI (open question 12) | Not deployed and not part of acceptance. The status query and the harness `describe` command show state | It costs memory on the infra node and no acceptance step reads it | M.1 |
| D11 | Namespace and retention | Namespace `wdf`, retention 1 day (estimate) | S3b's 100,000-job run stored 3,507 MiB at 1 day, and the `temporaldb` volume is 5,120 MiB. Dedup stays at submit (D4 of the epic) | M.2 |
| D12 | Where control activities run | `place` and `emit` are local activities (decided). `load_plan`, the cache activities, `commit` and `build_index` are regular activities on an unversioned control queue `wdf-control`, served by an activity worker inside the workflow worker service, which holds the database login of role `wdf_workflow` and the `workflow` and `indexer` store credentials | An index over many results can outlast a workflow task timeout, and the pool workers hold no database access | 0.1, A.1, A.2, D.1 |
| D13 | Cache key inputs | The cache key activity reads the source bundle, takes its `BundleInfo` with `wdf_compile.bundle.info` (the function the dispatch resolver uses) and the pins of `wdf_runtime.pins`, computes the key and memoises it in the process by (source hash, entry), because the key is a pure function of those | The plan holds the source reference but not the lockfile hash, and 100,000 jobs of one batch share one key | A.2 |
| D14 | `task.started` | The activity result and the failure details carry the worker's `started_at`, and the workflow emits `task.started` with that time when the attempt ends. An attempt that ends in a heartbeat timeout with no details emits none | The workflow cannot see an activity start, the worker emits no event (stages v1), and `time` is the time of the fact | 0.1, W.1 |
| D15 | Scheduler down | The `place` local activity retries with backoff to a 60 s ceiling and no attempt limit, with an alert metric, so a scheduler outage stalls placement and fails nothing. W.2 tests an outage longer than the workflow task timeout | The epic records behaviour beyond a workflow task timeout as unverified | W.2 |
| D16 | History budget (detail issue 12) | The workflow reports the history length that the SDK gives workflow code as a metric at each activation. W.2 measures events per job, per task and per retry against `history_events_per_job` (10), `_per_task` (8) and `_per_retry` (6) of job v2, and a measured figure above an estimate is raised then as a contract-change question on job v2 | The estimates are pinned and the real shape is unknown until the workflow exists | W.2 |

### 0.4 Proposals the lanes pin

After lane 0 merges, `contracts/activity/README.md`, `contracts/temporal/README.md` and their files are the source and this section is only a pointer.

| Item | Proposal | Epic item |
| --- | --- | --- |
| Contract directories | Two contracts, owner #6: `contracts/activity/` (contract 4 of the method) and `contracts/temporal/` (contract 5, whose `pools.yaml` the placement epic #9 takes over at its real values). Each has `VERSION`, `README.md`, `check.sh`, generated schemas and vectors under `vectors/<name>/{valid,invalid/<reason>}/`. Models live in `wdf_workflow.models`. Loader `python -m wdf_workflow.contract --check FILE` (exit 0 valid, 1 with `reason: <code>: <detail>` on stderr, 2 usage), generator `python -m wdf_workflow.gen [--check]` | method contracts 4 and 5 |
| Task input | `task-input.schema.json`: the activity's one argument, `{request, params, resume_counter, cache_key}`. `request` is the `dev.wdf.task.requested.v1` event (its `attempt` attribute is the task attempt), `params` the bound params of the plan task, `resume_counter` the workflow's counter so the worker forms the attempt identity with `wdf_keys.attempt_identity`, and `cache_key` the computed key for a compile task, `null` otherwise, which the compile worker re-checks at task start | decisions 10 and 12 |
| Task result | `task-result.schema.json`: `{outputs, log_tail, started_at, duration_s, peak_rss_mib}`, `outputs` a list of artifact references of the attempt, never their bytes | completion |
| Failure details | A reported failure is an `ApplicationError` whose `type` is the failure class and whose details validate against `failure-details.schema.json`: `{class, deterministic, reason, started_at, checkpoint, log_tail}`, `reason` at most 1,024 characters and starting with the class or the artifact code prefix, `checkpoint` a checkpoint reference or `null` | D2 |
| Heartbeat details | `heartbeat.schema.json`: `{progress, step, steps, checkpoint, started_at}`, where `progress`, `step` and `steps` come from the guest's `HEARTBEAT` line of workload v1 or the aggregate library's per-input line, and `checkpoint` is the latest uploaded reference. References only, never values | #4 and #10 Interfaces |
| Failure class table | `failure-classes.yaml`: one row per class, with its source (`workload` and the pool, `artifact`, `runner`, `temporal`, `placement`), `deterministic`, `budget` (`guest`, `infra`, `none`, `free`), and an `action` where one applies (`invalidate-cache-and-rebuild`, `previous-checkpoint`, `re-place`, `escalate-once`). Rows: every class of `contracts/workload/exit-classes.yaml`, with deterministic classes never retried and the rest per decision 22, D3 and D4; every code of `contracts/artifact/failures.yaml` (`STORE_UNAVAILABLE` and `OUTPUT_UPLOAD_FAILED` infra, `STORE_FORBIDDEN`, `ARTIFACT_LOST` and `OUTPUT_MISSING` not retried, `ARTIFACT_LOST` of a cache-sourced component `invalidate-cache-and-rebuild`, `CHECKPOINT_CORRUPT` infra with `previous-checkpoint`); runner classes `operator_interrupt` (free, advances the resume counter) and `worker_shutdown` (infra); Temporal timeouts `heartbeat_timeout` and `start_to_close_timeout` (infra) and `schedule_to_start_timeout` (`none`, `re-place`); placement verdicts `insufficient_memory`, `insufficient_cpu` and `no_matching_pool` (not retried). Loader reasons: `schema-violation`, `duplicate-class`, `class-not-covered` (a class or code of the two source tables has no row, read through package data), `deterministic-retried`, `unknown-budget` | open question 5, detail issue 5 |
| Naming | `contracts/temporal/naming.yaml`: namespace `wdf`, workflow type `JobWorkflow` on the versioned queue `wdf-workflow`, control activity queue `wdf-control`, pool queues `wdf-compile`, `wdf-simulate` and `wdf-aggregate`, activity names (`run_task` on the pool queues, `load_plan`, `cache_key`, `cache_lookup`, `cache_use`, `cache_record`, `cache_invalidate`, `commit`, `build_index` on `wdf-control`, local activities `place` and `emit`), worker deployment `wdf-workflow-worker` with a build id of the short commit, the reaper's Schedule id and queue as delivered, and search attributes `WdfTenant`, `WdfPriorityClass`, `WdfJobClass` and `WdfJobId` (keyword) and `WdfSubmittedAt` (datetime), the five that #8 proposes | detail issues 1, 2, the #8 start contract |
| Signals and query | `control-signal.schema.json` for `cancel`, `terminate` and `pause`: `{control_seq, reason}`, where a signal whose `control_seq` is not above the last applied one is ignored. `resume-signal.schema.json` for `resume`, sent by admission when it grants a slot: `{control_seq, manifest}`, `manifest` the patched manifest reference or `null`. `status-query.schema.json` for query `status`: job status, pause reason, resume counter, and per task its state (`pending`, `ready`, `placed`, `running`, `succeeded`, `skipped`, `failed`, `blocked`, `paused`, `cancelled`), task attempt, guest and infrastructure budget left, re-schedules and last checkpoint reference | detail issue 8, #24 Interfaces |
| Pool table v0 | `contracts/temporal/pools.yaml`, static, one slot per pod as `deploy/presets/slots/ci.yaml`: per pool its queue, `schedule_to_start_s` 1,800 (D5), `heartbeat_s` 60 (above S6's 30 s first run and well inside S3's cancel latency margin), and `start_to_close_s = run_timeout_s + 120` (a margin for the warm-up of decision 14 and staging). All are estimates, and #9 replaces the file with real values and formulas | method contract 5, decision 14 |
| Placer | `wdf_workflow.placement.Placer` with `place(request) -> Placement | Unschedulable`, `Placement(queue, schedule_to_start_s, start_to_close_s, heartbeat_s)`, and `StaticPlacer` over pool table v0. `wdf_workflow.testing.placer_contract(placer)` is the conformance test that #9's client runs | detail issue 4, #9 Interfaces |
| Fake pool worker | `wdf_workflow.testing.FakePool`: an activity worker that serves `run_task` on a pool queue with scripted outcomes (succeed with outputs written to a store or kept local, fail with a class, heartbeat a checkpoint reference, hang, die without completing, complete after its attempt was timed out). `wdf_workflow.testing.activity_contract(worker)` is the conformance test that #10's worker runs | stub for #10 |
| Service | `python -m wdf_workflow.service` runs the workflow worker (`JobWorkflow`, `PINNED`, build id from `WDF_BUILD_ID`), the control activity worker and the reaper's Schedule worker in one process, with SDK Prometheus metrics, the `wdf_temporal` converter and tracing interceptor, and a `SIGTERM` drain. Configuration from the environment only | detail issue 2 |
| Harness | `python -m wdf_workflow.harness {describe,smoke,restart,rollout} --address HOST:PORT ...`, which seeds job rows and manifests, starts workflows with the start options of the naming contract, runs `FakePool` workers on the host, and prints counters. Reused by #15 and #16 | detail issue 14 |

### Rules agreed with epics that do not exist yet

These are stated so the owner epic confirms them in its own spec.

| Epic | Rule |
| --- | --- |
| #9 Placement | Ships a `Placer` that passes `placer_contract`, replaces `contracts/temporal/pools.yaml` with real values and timeout formulas by a contract-change PR, keeps the queue names of the naming contract, and owns the escalation verdict that `escalate-once` reads |
| #10 Workers | Serves `run_task` with the task input, task result, failure details and heartbeat details of `contracts/activity/` and passes `activity_contract`, reports the classes of the failure class table, puts the latest checkpoint reference in both heartbeat and failure details, heartbeats during the warm-up of decision 14, and keeps every pool queue unversioned |
| #8 Admission | Starts `JobWorkflow` on `wdf-workflow` with `workflow_id(job_id)`, `REJECT_DUPLICATE`, the five search attributes and the accepted event and params block as the two arguments, and sends `resume` with `control_seq` and the row's pending patch when it grants a resumed job a slot |
| #24 Dispatch and control | Sends `cancel`, `terminate` and `pause` with the row's `control_seq`, retries until delivered, and reads `status` for detail |
| #18 Checkpoint | Builds segments and continue-as-new on this epic's workflow, retry loop, pause state and resume signal (D7), and owns the checkpoint format behind the references |
| #13 Observability | Reads the workflow's metrics: history length, retries by budget, re-schedules, paused jobs, replay failures, and the Temporal `cache_usage` series and `memory.events` `max` counter named in M.2 |
| #15, #16 | Drive `wdf_workflow.harness` for restarts, rollouts and the standard load, and vary the two cache keys, Temporal memory and CPU, and Postgres memory and disk |

## 1. Goal and non-goals

Goal: Temporal is the single owner of job lifecycle, task dependencies, retries and the task backlog.
One admitted job is one workflow that runs the validated plan: it places each ready task on a pool queue, drains on failure, retries, pauses and resumes long jobs from their last checkpoint, and ends with a terminal result.
Workflow code is versioned from the start, so a job that runs for days finishes on the code it started on.
Workflow code is plain deterministic Python in the workflow worker and is not WASM.

### Breakdown inputs updated for what earlier epics landed

The epic body was written before the envelope, validator, state, artifacts and toolchain epics closed, and these changes apply.

| Epic body item | Now | Source |
| --- | --- | --- |
| Contract versions | events v3, job v2, artifact v3, workload v1, status v1, stages v1, state v2, store v3 | the `VERSION` files [read] |
| Compile cache check | The five-input key of decision 9, looked up and recorded by the workflow (decision 10), with the worker's re-check at task start kept from the #10 body | events v3, artifacts decision 18, toolchain decisions 5 and 6 |
| "A compile that fails deterministically (the source, a dependency or the WIT conformance check is wrong)" | Corrected: `compile_failed` (source or WIT conformance) is deterministic, while a locked dependency that the mirror cannot serve is `compile_dependency_unavailable`, non-deterministic, retried as infrastructure, and `compile_timeout` and `compile_oom` are classes of their own | `contracts/workload/exit-classes.yaml` [read] |
| Aggregate failure classes | `aggregate_parse_failed`, `aggregate_input_corrupt` and `aggregate_input_refused` are deterministic, `aggregate_input_unavailable` is an infrastructure failure, and `aggregate_failed` is a library bug, retried from the guest budget | `contracts/workload/exit-classes.yaml` [read] |
| Heartbeats | A guest writes `HEARTBEAT {"progress":f,"step":n,"steps":N}` on stderr and the aggregate library one such line per input, which the runner of #10 turns into heartbeat details whose shape this epic pins | workload v1 README [read] |
| Guest minimum and warm-up | `GUEST_MIN_PAGES` 209 and the deterministic `guest_memory_cap` refusal, and the per-pod warm-up of about 30 s at 250m, which sets the v0 heartbeat timeout and start-to-close margin | decision 14 |
| Workflow input | The accepted event plus the params block | decision 4 |
| Open questions 1, 2, 3, 10, 11 | Decided elsewhere | decisions 5 to 8 |
| Temporal's Postgres | Delivered by #3 as `deploy/temporaldb/` (Postgres 16.14, 1,024 MiB, databases `temporal` and `temporal_visibility`) | `deploy/temporaldb/` [read] |
| SDK and server | `temporalio` 1.35.0 in `uv.lock` (S3 ran 1.34.0 with server 1.32.1), the T1 dev server of the CLI image `temporalio/temporal` 1.9.1, and no chart or server image pinned (`charts: {}`) | `uv.lock:529-530`, `deploy/versions.yaml` [read] |
| Temporal budget | 1,024 MiB in the `wdf-infra` sum, and 256 MiB for the workflow worker in the `wdf-control` reserve | S8 (`docs/delivery/spikes/S8/state-footprint.md:159-166`) [read] |
| The reaper's Schedule worker | Deployed by this epic (decision 16) | artifacts closure |
| The diagram | Shows "async completion", "max_attempts always set" and a long or short job deciding pause or fail, all three overtaken (native completion, single attempt, D9) | `docs/architecture/12-dag-and-workflow.drawio` [read] |
| Continue-as-new | #18's Interfaces row gives it to #6, while the #6 body says none in v1: kept out of this pass (D7) | #6 and #18 bodies |

### Delivered already, and what is left

| Epic body item | State on `main` | Where it is, or what is left |
| --- | --- | --- |
| Event converter, tracing interceptor, replay corpus and runner | Delivered by #4 | `packages/temporal/` (`wdf_temporal`) |
| Key formulas (`workflow_id`, `attempt_identity`, `cloudevent_id`, `compile_cache_key`) | Delivered by #4 | `packages/keys/` |
| Plan, binding, stats, patch check | Delivered by #5 | `packages/plan/`, `packages/jobpatch/` |
| Event store, guarded transition, roles | Delivered by #3 | `packages/eventstore/`, `packages/jobstate/`, `R__roles.sql` |
| Commit, cache client, aggregate index builder and reader, checkpoint verify | Delivered by #12 | `packages/artifactclient/` |
| Reaper and its Schedule | Delivered by #12, deployment left | decision 16, D.1 |
| Temporal's Postgres | Delivered by #3 | `deploy/temporaldb/` |
| Temporal module, bootstrap, keys | Left | S10, M.1, M.2 |
| Activity and Temporal contracts, pool table v0 | Left | 0.1 |
| Workflow worker service and image | Left | D.1, D.2 |
| Job workflow and every behaviour of the epic | Left | P.1, P.2, A.1, A.2, W.1 to W.5 |
| Versioning, determinism gate, durability harness | Left | S9, V.1, V.2, V.3 |

Non-goals for this pass:

- everything the epic lists under "Does not own": admission, intake and the start call (#8, #7), the placement policy, real pool table and placement service (#9), the worker process, runner and classification (#10), the checkpoint contract and retention (#18), the plan format (#5), envelopes (#4), the job row and event table (#3), artifact storage (#12), the API handlers (#24), dashboards and alerts (#13) and cluster sizing (#2)
- segments and continue-as-new (D7), and the checkpoint format (decision 15)
- the standard load of 100,000 jobs on the Helm install, which is #16's run

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with `uv`, Python, `helm`, `kubectl`, `kind`; recipes `just test`, `just itest`, `just ci`, `just up`, `just down`, `just nuke`; `wdf_itest` | `flake.lock`, `uv.lock` (`temporalio` 1.35.0) | none, exists [read] |
| Cluster | Preset `ci` (infra node 5,120 MiB, `wdf-infra` quota 4,772 MiB, `wdf-control` on the infra pool), module contract, topology, namespaces, cluster lock | `deploy/presets/ci.yaml`, module v1, topology v1 | none, exists |
| Envelope | Event models and codec, key formulas, status table, stage table and `run_stage_contract`, `wdf_temporal` converter, tracing, corpus and replay runner, `wdf_compat` window | events v3, keys v1, status v1, stages v1, temporal v1, compat v1 | none, exists |
| Validator | Plan models, `read_plan`, `bind`, `stats`, `patch_check`, limits `ci` (history estimates, `compile_run_timeout_s` 600, `compile_retries` 1) | job v2, plan v1, patch v1 | none, exists |
| State | `transition`, `insert_events`, role `wdf_workflow`, `deploy/temporaldb/`, store roles `workflow` and `indexer` | state v2, store v3, eventstore v1, jobstate v1 | none, exists |
| Artifacts | `commit`, `cache.lookup`, `use`, `record`, `invalidate`, `build_index`, `put_index`, `verify_checkpoint`, typed errors, failure taxonomy, staging, index format, reaper Schedule | artifact v3, artifactclient v1, artifactreaper v1 | none, exists |
| Toolchain | Exit class table, `wdf_runtime.pins`, `wdf_compile.bundle.info` | workload v1, runtime v1, compile v1 | none, exists |
| Placement (#9) | `place` and the real pool table | not built (E9) | `StaticPlacer` over pool table v0 |
| Workers (#10) | Pool activity workers | not built (E11) | `FakePool` |
| Admission (#8) | The start call and the `resume` signal | not built (E12) | tests and the harness start workflows and send `resume` themselves |
| Dispatch (#24) | Control signals | not built (E13) | tests send signals directly |
| Checkpoint (#18) | Checkpoint format | not built (E17) | checkpoint references written by `write_checkpoint` with fake content |

No `revisit` child issue is open against an earlier epic that blocks this epic, and this spec adds none (decisions 19 and 20 keep every pinned contract and the cluster preset as they are).
The names below were checked free on `main` [read: no `packages/workflow/`, no `contracts/activity/`, no `contracts/temporal/`, no `deploy/temporal/`, no `deploy/workflow/`, no `docs/delivery/spikes/S9/` or `S10/`].

### Produces (each pinned precisely)

| To | What | Where it lives (path) | Shape | Version |
| --- | --- | --- | --- | --- |
| #10, #9, #18, #13, #15 | Activity contract | `contracts/activity/` | `task-input`, `task-result`, `failure-details`, `heartbeat` schemas, `failure-classes.yaml`, vectors, loader and reasons as 0.4 | activity v1 |
| #8, #9, #10, #24, #12, #13, #15, #16 | Temporal contract | `contracts/temporal/` | `naming.yaml`, `pools.yaml` v0, `control-signal`, `resume-signal` and `status-query` schemas, vectors, loader | temporal v1 |
| #8, #24, #17, #18 | Job workflow and worker service | `packages/workflow/` (`wdf_workflow`), `deploy/workflow/` | `JobWorkflow.run(accepted, params)`, `python -m wdf_workflow.service`, module `workflow-worker` in `wdf-control` | workflow v1 |
| the cluster, every epic | Temporal module | `deploy/temporal/` | module v1 manifest, the pinned chart or the fallback of decision 20, bootstrap job, keys | module v1 |
| #9, #10 | Conformance tests and stubs | `wdf_workflow.placement`, `wdf_workflow.testing` | `Placer`, `StaticPlacer`, `placer_contract`, `FakePool`, `activity_contract` | workflow v1 |
| #15, #16, #17 | Harness | `wdf_workflow.harness` | `describe`, `smoke`, `restart`, `rollout` | workflow v1 |
| #4 corpus | Recorded histories of `JobWorkflow` | `packages/temporal/corpus/<release>/`, one line in `owners.yaml` | corpus files of `wdf_temporal.corpus` | none |
| #2, #16 | Spike results | `docs/delivery/spikes/S9/versioning.md`, `docs/delivery/spikes/S10/helm-install.md` | pass or fail, numbers, decision | none |

## 3. Lanes

Package names are proposals.
Lane 0 owns every file it creates until the owning lane takes over the glob, except the contract modules of `wdf_workflow` and `wdf_workflow.testing`, which stay lane 0's.

Departures from the method, stated so the review approves them knowingly:

- Spikes S9 and S10 run beside lane 0, because they write only under `docs/delivery/spikes/`.
  S10 drives the cluster under the lock and gates M.1, and S9 gates V.2.
- Lane W is one worker in sequence, because the workflow class is one file whose behaviours each issue extends.
- `docs/delivery/method.md` is not edited by this spec; its verifier table still lists `verify-execution`, a follow-up the earlier specs record the same way.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Contracts and skeleton | 1 | `contracts/activity/**`, `contracts/temporal/**`, `packages/workflow/{pyproject.toml,README.md}`, `packages/workflow/src/wdf_workflow/{__init__,models,contract,gen,placement}.py`, `packages/workflow/src/wdf_workflow/testing/**`, `packages/workflow/tests/{conftest,test_loader,test_drift,test_placer_contract,test_activity_contract,test_reaper_names,test_wheel}.py`, the root `pyproject.toml` workspace edit, the `uv.lock` workspace entry, one line of `packages/temporal/corpus/owners.yaml`, two rows of `docs/contracts.md`, `docs/architecture/15-contracts.drawio` and its svg, `docs/architecture/12-dag-and-workflow.drawio` and its svg | 0.1, 0.2 | nothing (barrier, merged alone) |
| S Spikes | 2 (one per spike) | `docs/delivery/spikes/S9/**`, `docs/delivery/spikes/S10/**`, two rows of `docs/delivery/spikes/README.md` | S9, S10 | nothing, beside lane 0 |
| M Temporal module | 1 | `deploy/temporal/**`, the keys `charts.temporal`, `images.temporalio/server` and `images.temporalio/admin-tools` of `deploy/versions.yaml` | M.1, M.2 | lane 0, S10 |
| P Pure rules | 1 | `packages/workflow/src/wdf_workflow/{ready,result,failures,budgets}.py`, `packages/workflow/tests/test_{ready,result,failures,budgets}.py` | P.1, P.2 | lane 0 |
| A Control activities | 1 | `packages/workflow/src/wdf_workflow/activities/**`, `packages/workflow/tests/activities/**` | A.1, A.2 | lane 0 |
| W Job workflow | 1 | `packages/workflow/src/wdf_workflow/{job,taskpath,control,pause,aggregate}.py`, `packages/workflow/tests/workflow/**` | W.1, W.2, W.3, W.4, W.5 | lane 0, P.1, P.2 |
| D Service and deployment | 1 | `packages/workflow/src/wdf_workflow/service/**`, `packages/workflow/tests/service/**`, `deploy/workflow/**`, the key `images.python` of `deploy/versions.yaml` | D.1, D.2 | lane 0, M.1 for D.2 |
| V Versioning, determinism, harness | 1 | `packages/workflow/src/wdf_workflow/{versioning.py,harness/**}`, `packages/workflow/tests/{replay,versioning,harness}/**`, `packages/temporal/corpus/<this epic's release>/**` | V.1, V.2, V.3 | S9, W.1 to W.5, D.1, D.2 |

Shared-file rules:

- `uv.lock` is the one hotspot, and 0.1 adds only the workspace entry of `wdf-workflow`.
  A lock conflict is never resolved by hand, and `uv lock` is rerun after rebase.
- `deploy/versions.yaml` gets one key per line, and a PR touches only its own keys (M's three, D's one).
- `packages/workflow/src/wdf_workflow/__init__.py` and `tests/conftest.py` are lane 0's, and a lane imports by module path.
- `contracts/activity/README.md` and `contracts/temporal/README.md` are lane 0's, and after lane 0 merges a pinned file changes only through a contract-change PR.
- `docs/architecture/12-dag-and-workflow.drawio` and its svg are refined by 0.2 before any other lane starts (native completion, single attempt with the loop in the workflow, pause keyed on the checkpoint flag, the control activities), and the exit-test worker re-checks them at closure.
- `packages/temporal/corpus/owners.yaml` gets one appended line, `JobWorkflow: "#6 (workflow engine)"`.
- `justfile`, `just/*.just`, `.github/workflows/*`, `nix/**`, `scripts/*`, every other package and every other `deploy/` directory are not touched.
- Generated schemas are committed, regenerated by `python -m wdf_workflow.gen`, and never hand edited.
- No tracked file holds a credential: secrets come from the state directory as the `temporaldb` module does.

## 4. Issues

One block per issue, in the [lane issue template](../../../.github/ISSUE_TEMPLATE/lane-issue.md), replaced by `#NNN` links after filing.
Done means, for every issue: `just ci` is green on the branch, the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files changed.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 Temporal module via Helm | S10, M.1, M.2 |
| 2 Workflow worker service | D.1, D.2 |
| 3 Job workflow v1 | P.1, W.1 |
| 4 Task path and `place` integration, compile cache check | 0.2 (`StaticPlacer`), W.1, A.2 |
| 5 Retry loop and failure budgets | 0.1 (the table), P.2, W.2 |
| 6 Schedule-to-start re-schedule | P.2, W.2 |
| 7 Aggregate semantics | P.1, A.2, W.3 |
| 8 Cancellation and control signals | W.4 |
| 9 Pause and resume | W.5 |
| 10 Worker versioning and upgrade policy | S9, V.2 |
| 11 Determinism gate | V.1 |
| 12 History budget | W.1 (the metric), W.2 (the shape), continue-as-new readiness moved to #18 (D7) |
| 13 State-change events | A.1 |
| 14 Durability test harness | V.3 |

### 0.1 Activity and Temporal contracts

Lane 0, tier T0. Needs: nothing. Blocks: every other issue but the spikes.

- Scope: the two contracts of 0.4 with their models, loader, generator, vectors and READMEs, the two `docs/contracts.md` rows and the contracts diagram.
- Owns: the contract globs and the model, loader and generator modules of lane 0.
- Consumes / produces: events v3, workload v1, artifact v3 and job v2 are read through package data. Produces activity v1 and temporal v1.
- Acceptance criteria:
  - [ ] `contracts/activity/check.sh` and `contracts/temporal/check.sh` exit 0 for every `vectors/*/valid/*` file and 1 with the `reason:` of its directory for every `invalid` one, shown by `pytest packages/workflow -v -k loader`.
  - [ ] `test_failure_table_covers_exit_classes` and `test_failure_table_covers_artifact_codes` fail when a class of `exit-classes.yaml` or a code of `failures.yaml` has no row, shown by a vector under `invalid/class-not-covered/`.
  - [ ] `python -m wdf_workflow.gen --check` exits 0, and exits 1 after a model field is renamed.
  - [ ] `scripts/check-contracts-map.sh` and `scripts/export-diagrams.sh --check` exit 0.
- Verifier: `packages/workflow/tests/test_loader.py`, `test_drift.py`.

### 0.2 Package skeleton, stubs and fakes

Lane 0, tier T0, and T1 for the fake pool worker. Needs: 0.1. Blocks: every code issue.

- Scope: `packages/workflow/` with its wheel test, `Placer`, `StaticPlacer` and `placer_contract`, `FakePool` and `activity_contract`, the reaper name test, the corpus owners line, and the refined DAG diagram.
- Owns: the rest of lane 0's globs.
- Consumes / produces: pool table v0, the naming contract. Produces the stubs of section 5.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "placer_contract or activity_contract"` passes against `StaticPlacer` and `FakePool`, and fails against a placer that returns a queue outside the naming contract and a fake that returns bytes instead of references.
  - [ ] `pytest packages/workflow -v -k reaper_names` passes, and fails when the naming contract's reaper queue differs from `wdf_artifactreaper.schedule.TASK_QUEUE`.
  - [ ] `pytest packages/workflow -v -k wheel` builds the wheel and loads both contracts from package data outside the repository.
  - [ ] `scripts/export-diagrams.sh --check` exits 0 after the diagram refinement.
- Verifier: `test_placer_contract.py`, `test_activity_contract.py`, `test_reaper_names.py`, `test_wheel.py`.

### S9 Versioning on SDK 1.35.0 and the pinned-bug override

Lane S, spike, timebox 1.5 days, tier T1, throwaway code in `docs/delivery/spikes/S9/code/`. Needs: nothing. Blocks: V.2.

- Question: does pinned versioning hold on `temporalio` 1.35.0 against the pinned dev server, which call sets the current version, and can an open pinned workflow be moved to a fixed build?
- Pass line, all four on the dev server of `temporalio/temporal` 1.9.1 with `temporalio` 1.35.0: (1) a `PINNED` workflow started on build A and waiting on a signal finishes on A while B is current; (2) the current version is set by a call the platform can make (an SDK client call if 1.35.0 has one, otherwise the CLI or the raw gRPC call), named in the result; (3) a versioning override moves an open pinned workflow from A to B, and it completes on B's code; (4) an unversioned activity worker on a pool queue serves workflows of both builds.
  Any of the four failing is a fail.
- On fail: the override procedure is "pause, terminate and resubmit the job, keeping its artifacts", recorded in the result and applied by V.2.
- Result: `docs/delivery/spikes/S9/versioning.md` with the server version the dev server reports.

### S10 The official Temporal chart on the `ci` preset

Lane S, spike, timebox 2 days, tier T2 under the cluster lock, throwaway values in `docs/delivery/spikes/S10/code/`. Needs: nothing. Blocks: M.1.

- Question: does the pinned official chart with Postgres persistence and SQL visibility on `temporaldb`, one replica per service, 512 shards and the two dynamic-config keys fit Temporal's 1,024 MiB and 1 CPU on the `ci` infra node, and do the keys reach the history service?
- Pass line, all of: (1) frontend, history, matching and worker run at one replica each with no Elasticsearch and no web UI, and the sum of their memory limits is at most 1,024 MiB and of their CPU limits at most 1; (2) 3,000 two-task stand-in jobs (the S3 method, with host workers through a port-forward bound to 127.0.0.1) all finish; (3) `cache_usage{cache_type="mutablestate"}` of the history service stays at or below 1,000 while they finish; (4) the `max` counter of `memory.events` of no Temporal pod rises.
  Any of the four failing is a fail.
- On fail: decision 20 applies, and M.1 deploys the one-pod shape.
- Result: `docs/delivery/spikes/S10/helm-install.md` with the chart and server versions and digests, the per-service limits and peaks, and the decision.

### M.1 Temporal module

Lane M, tier T2. Needs: 0.1, S10. Blocks: M.2, D.2.

- Scope: `deploy/temporal/` per the module contract, the pinned chart (or the fallback of decision 20), persistence and visibility on `temporaldb`, 512 shards, one replica per service, the two keys in the dynamic-config values, limits from S10, readiness, `profiles: [dev, full, ci]`.
- Owns: lane M's globs.
- Consumes / produces: module v1, `deploy/temporaldb/`, S10. Produces module `temporal` and three `deploy/versions.yaml` keys.
- Acceptance criteria:
  - [ ] `just versions --check` exits 0 with the chart and the two images pinned by digest.
  - [ ] `scripts/tilt/run.sh ci` after `just up` exits 0 with `temporal` healthy beside `temporaldb`, and `kubectl -n wdf-infra get pods` lists one pod per Temporal service (or one under the fallback).
  - [ ] A `helm template` render test shows both keys in the dynamic config and the limits of S10.
- Verifier: the commands above, output pasted.

### M.2 Bootstrap and keys-active acceptance

Lane M, tier T2. Needs: M.1. Blocks: the exit test.

- Scope: an idempotent bootstrap job that creates namespace `wdf` with the retention of D11 and registers the five search attributes, and the acceptance checks of the epic's detail issue 1.
- Owns: lane M's globs.
- Consumes / produces: naming contract. Produces the namespace and search attributes.
- Acceptance criteria:
  - [ ] The bootstrap job completes twice in a row with exit 0, and the second run changes nothing.
  - [ ] `python -m wdf_workflow.harness describe --address 127.0.0.1:<port>` (from V.3, or the equivalent `temporal operator` calls before V.3 merges) prints retention `24h` and the five search attributes with their types.
  - [ ] After 2,000 jobs finish, `cache_usage{cache_type="mutablestate"}` is at or below 1,000 and the `max` counter of `memory.events` of the history pod is unchanged.
- Verifier: the commands above, output pasted.

### P.1 Ready set and job result model

Lane P, tier T0. Needs: lane 0. Blocks: W.1.

- Scope: pure functions with no Temporal import: the ready set in ascending task id under `max_parallel`, fan-out and fan-in, blocked dependents, aggregate readiness with and without `allow_partial`, both drain modes (D8), pause or fail (D9, decision 23), the terminal status (`succeeded`, `partial`, `failed`, `paused`, `cancelled`, `terminated`), `skipped_tasks` of a partial run, and the per-task states of the status query.
- Owns: lane P's globs for `ready` and `result`.
- Consumes / produces: plan v1, status v1, the status query schema. Produces `wdf_workflow.ready`, `wdf_workflow.result`.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "ready or result"` passes, including Hypothesis properties: no task becomes ready before its dependencies succeed, at most `max_parallel` tasks run, the order is a function of the plan alone, and every task ends with a state.
  - [ ] Named cases pass: `test_aggregate_waits_for_all`, `test_allow_partial_lists_skipped`, `test_paused_and_failed_chain_pauses`, `test_resume_reruns_unsucceeded`.
- Verifier: `test_ready.py`, `test_result.py`.

### P.2 Failure decisions and budgets

Lane P, tier T0. Needs: lane 0. Blocks: W.2.

- Scope: `decide(failure, task, budgets) -> Decision` over the failure class table: guest and infrastructure budgets, deterministic classes exhausting the guest budget at once, `operator_interrupt` free with the resume counter advanced, the OOM rule of decision 22, the backoffs of D3 and D5, `invalidate-cache-and-rebuild`, `previous-checkpoint` and `re-place`, and the history estimate per decision.
- Owns: lane P's globs for `failures` and `budgets`.
- Consumes / produces: activity v1 table, plan `retries`. Produces `wdf_workflow.failures`, `wdf_workflow.budgets`.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "failures or budgets"` passes, with one case per row of the table (`test_every_class_row_has_a_case`).
  - [ ] `test_deterministic_class_not_retried`, `test_infra_cap_exhausted`, `test_operator_interrupt_free` and `test_schedule_to_start_backoff_ceiling` pass.
- Verifier: `test_failures.py`, `test_budgets.py`.

### A.1 Plan load and the emit point

Lane A, tier T1 (Postgres and SeaweedFS through `wdf_itest`, as plain functions without Temporal). Needs: lane 0. Blocks: A.2, W.5 at T2.

- Scope: `load_plan` (D1), the batched `emit` local activity that writes events with `insert_events` and projects status with `transition` under `wdf_workflow`, deterministic ids from `cloudevent_id` and `time` from the fact, and `Lost` handling when the row moved.
- Owns: lane A's globs.
- Consumes / produces: state v2, eventstore v1, jobstate v1, artifactclient v1, plan v1. Produces the two activities.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "load_plan or emit"` passes, including `test_emit_replay_inserts_nothing` (a second emit of the same batch inserts 0 rows) and `test_manifest_hash_mismatch_refused`.
  - [ ] `test_emit_lost_when_row_cancelled` shows a `Lost` answer handed to the workflow and no exception.
- Verifier: `tests/activities/test_load_plan.py`, `test_emit.py`.

### A.2 Commit, compile cache and aggregate index activities

Lane A, tier T1. Needs: A.1. Blocks: W.3 at T2, V.3.

- Scope: `commit` of the accepted attempt (decision 11), `cache_key` (D13), `cache_lookup`, `cache_use`, `cache_record` with the build-info key check, `cache_invalidate`, and `build_index` from recorded results in task id order with the `indexer` role.
- Owns: lane A's globs.
- Consumes / produces: artifactclient v1, keys v1, runtime v1 pins, compile v1 `bundle.info`. Produces the remaining control activities.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "commit or cache or build_index"` passes, including `test_cache_error_not_miss`, `test_build_info_key_mismatch_refused`, `test_commit_refuses_zombie_attempt` and `test_index_in_task_id_order`.
  - [ ] `test_cache_key_matches_build_info` computes the key of the workload bundle of `wdf_workloads.bundle` and finds it equal to the `cache_key` printed by `python -m wdf_compile build` for that bundle (a slow test, run in the exit test).
- Verifier: `tests/activities/test_commit.py`, `test_cache.py`, `test_index.py`.

### W.1 Job workflow v1 and the task path

Lane W, tier T1 (dev server through `wdf_itest`, with fake control activities and `FakePool`). Needs: lane 0, P.1. Blocks: W.2.

- Scope: `JobWorkflow.run(accepted, params)` with the payload converter and tracing interceptor of `wdf_temporal`, plan load, the ready loop, `place` as a local activity through `StaticPlacer`, a single-attempt activity on the returned queue with the returned timeouts, the compile cache check before placement (skip on a hit with `task.skipped`), commit, emit, the status query, the history length metric, and `run_stage_contract("workflow", ...)`.
- Owns: lane W's globs.
- Consumes / produces: P.1, the naming and activity contracts. Produces the workflow.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "fan_out_fan_in or max_parallel or cache_hit_skips or status_query or stage_contract_workflow"` passes.
  - [ ] `test_place_answer_recorded` shows the placement as a marker in the history and no `place` call on replay.
- Verifier: `tests/workflow/test_job.py`, `test_taskpath.py`.

### W.2 Retry loop, schedule-to-start and lost completion

Lane W, tier T1. Needs: W.1, P.2. Blocks: W.4.

- Scope: the decisions of P.2 in the workflow, backoff timers, the re-place after a schedule-to-start timeout, the scheduler-down behaviour of D15, and the history shape of D16.
- Owns: lane W's globs.
- Consumes / produces: P.2. Produces the loop.
- Acceptance criteria:
  - [ ] `test_lost_completion_job_finishes`: a `FakePool` worker does the work and never completes, the heartbeat timeout is retried from the infrastructure budget, and the job finishes with only the second attempt committed.
  - [ ] `test_zombie_fenced`: a timed-out attempt's next heartbeat is refused, and its outputs are never committed.
  - [ ] `test_schedule_to_start_replaces`, `test_scheduler_down_beyond_task_timeout` and `test_deterministic_class_not_retried_in_workflow` pass.
  - [ ] `test_history_shape` prints events per job, per task and per retry, and the PR records them against 10, 8 and 6.
- Verifier: `tests/workflow/test_retry.py`.

### W.3 Aggregate semantics

Lane W, tier T1. Needs: W.2. Blocks: W.4.

- Scope: the `build_index` call before an aggregate task, the aggregate-only job taking its index reference from its parameters, `allow_partial`, and the `partial` terminal status with `job.partial`.
- Owns: lane W's globs.
- Consumes / produces: P.1, the index activity name. Produces aggregate handling.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "aggregate_waits_for_all_inputs or allow_partial_partial_status or aggregate_only_job or aggregate_gets_index_reference_only"` passes.
- Verifier: `tests/workflow/test_aggregate.py`.

### W.4 Cancellation and control signals

Lane W, tier T1. Needs: W.3. Blocks: W.5.

- Scope: `cancel`, `terminate` and `pause` with `control_seq` dedup, catching both the asyncio and the Temporal cancelled exception, cancelling in-flight tasks and re-raising, the manual pause of decision 7 with `operator_interrupt`, and projection of `cancelled`, `terminated` and `paused`.
- Owns: lane W's globs.
- Consumes / produces: signal schemas. Produces control handling.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "cancel_in_flight or terminate_hard_stop or signal_dedup or manual_pause_interrupts_checkpoint_task or manual_pause_drains_others"` passes.
- Verifier: `tests/workflow/test_control.py`.

### W.5 Pause and resume

Lane W, tier T1, and T2 in the exit test. Needs: W.4, A.1. Blocks: V.1.

- Scope: pause on exhausted budgets per D9 and decision 23, waiting on `resume` with no polling, a patched manifest reloaded through `load_plan`, the compile of a patched source re-run (cache keyed) before the resume, the job paused again when that compile fails, resume from the last good checkpoint with the next resume counter, and the previous checkpoint after `CHECKPOINT_CORRUPT`.
- Owns: lane W's globs.
- Consumes / produces: resume signal schema, patch v1. Produces pause and resume.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "drain_then_pause or resume_from_checkpoint or resume_compile_failure_stays_paused or corrupt_checkpoint_falls_back or paused_costs_no_history"` passes, where the last shows the history length unchanged across a 60 s pause.
- Verifier: `tests/workflow/test_pause.py`.

### D.1 Workflow worker service

Lane D, tier T1. Needs: lane 0. Blocks: D.2, V.2.

- Scope: `python -m wdf_workflow.service` as 0.4, configuration from the environment, SDK Prometheus metrics, graceful shutdown, and the reaper's Schedule worker in the same process.
- Owns: lane D's globs.
- Consumes / produces: naming contract, `wdf_artifactreaper.schedule`. Produces the service.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "service_starts_three_workers or sigterm_drains or metrics_endpoint"` passes against the dev server.
- Verifier: `tests/service/test_service.py`.

### D.2 Image and deployment

Lane D, tier T2. Needs: D.1, M.1. Blocks: V.2, V.3.

- Scope: the service image on a pinned Python base, module `workflow-worker` in `wdf-control` with requests equal to limits at 256 MiB (S8's reserve, an estimate), the database login for `wdf_workflow` and the store credentials from the state directory, readiness, and `WDF_BUILD_ID` from the image.
- Owns: lane D's globs.
- Consumes / produces: module v1, namespaces. Produces module `workflow-worker`.
- Acceptance criteria:
  - [ ] `scripts/tilt/run.sh ci` after `just up` exits 0 with `workflow-worker` healthy, and its pod log shows the three workers polling.
- Verifier: the command above, output pasted.

### V.1 Determinism gate

Lane V, tier T1. Needs: W.5. Blocks: the exit test.

- Scope: recorded histories of `JobWorkflow` covering a plain job, a retry, a cache hit, a partial aggregate, a cancel and a pause with resume, replayed through `wdf_temporal.corpus.replay_corpus` in CI, a nondeterminism negative test, and the rules for changes in `packages/workflow/README.md`.
- Owns: lane V's globs.
- Consumes / produces: the corpus of #4. Produces histories and the gate.
- Acceptance criteria:
  - [ ] `pytest packages/temporal packages/workflow -v -k replay` passes, and `test_replay_detects_reordered_activity` fails the replay of a history against a changed workflow.
- Verifier: `tests/replay/test_replay.py`.

### V.2 Worker versioning and rollout

Lane V, tier T1, and T2 in the exit test. Needs: S9, D.1, W.1. Blocks: the exit test.

- Scope: the rollout helper with S9's call, the pinned behaviour test, old build drain detection (no open pinned workflow left), and the override procedure that S9 settles.
- Owns: lane V's globs.
- Consumes / produces: S9. Produces `wdf_workflow.versioning`.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "pinned_finishes_on_old_build or drain_detects_no_pinned or override_procedure"` passes.
- Verifier: `tests/versioning/test_versioning.py`.

### V.3 Durability harness

Lane V, tier T1, and T2 in the exit test. Needs: W.2, A.2, D.2. Blocks: the exit test.

- Scope: `python -m wdf_workflow.harness` with `describe`, `smoke`, `restart` (the server or the workflow worker with tasks in flight) and `rollout`, seeding rows and manifests and running `FakePool` workers on the host.
- Owns: lane V's globs.
- Consumes / produces: every lane. Produces the harness for #15 and #16.
- Acceptance criteria:
  - [ ] At T1, `pytest packages/workflow -v -k harness_restart_worker` passes with all jobs finished.
  - [ ] At T2, `harness smoke --jobs 200` prints `finished: 200` and `failed: 0`.
- Verifier: `tests/harness/test_harness.py`, the T2 command output pasted.

## 5. Order, stubs and spikes

- First, in parallel: 0.1, S9 and S10.
  Then 0.2.
  Lane 0 (0.1, 0.2) merges alone, with an estimate of four days.
- After lane 0: P.1, P.2, A.1 and D.1 start together, and M.1 after S10.
- After P.1: W.1. After W.1 and P.2: W.2. Then W.3, W.4 and W.5 in order, W.5 also after A.1. After A.1: A.2. After M.1: M.2, and D.2 after D.1. After S9, D.1 and W.1: V.2. After W.2, A.2 and D.2: V.3. After W.5: V.1.
- DAG: (0.1 ‖ S9 ‖ S10), then 0.2, then (P.1 ‖ P.2 ‖ A.1 ‖ D.1 ‖ M.1), then (W.1 ‖ A.2 ‖ M.2 ‖ D.2), then (W.2 ‖ V.2), then (W.3 ‖ V.3), then W.4, then W.5, then V.1, then the exit test.
- Cluster work: S10, M.1, M.2, D.2, the T2 halves of V.2 and V.3, and the exit test, each under the cluster lock, one at a time.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| `StaticPlacer` over pool table v0 | the placement service of #9 | #9 runs `placer_contract` against its client and replaces `pools.yaml` by a contract-change PR |
| `FakePool` | the pool workers of #10 | #10 runs `activity_contract` against its worker on each pool queue |
| Workflow starts and `resume` signals sent by tests and the harness | admission (#8) | #8 starts with the options of the naming contract, and the harness start path is its reference |
| Control signals sent by tests | the control API (#24) | #24 sends the signal schemas of the temporal contract |
| Checkpoint references with fake content | the checkpoint format of #18 | #18's kill-and-resume test runs on this workflow |
| Fake control activities in W tests | the activities of lane A | the T2 rows of the exit test run the real activities, because T1 holds at most two containers |

Spikes: S9 and S10, as their blocks in section 4 state, run beside lane 0 and gate V.2 and M.1.

## 6. Verifier delta

None, per decision 1: no skill, no feature file, no verify recipe.
Each issue names its tests in section 4, and the exit test in section 7 proves the epic from a clean export.

## 7. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main` taken outside any git repository, on the 20 GiB WSL2 host with Docker running.
Containers other than the ones the run starts are never touched.
Rows 9 to 15 take the cluster lock for their whole sequence (`flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 bash -c '<commands>'`).
`$W` is a scratch directory outside the export, and `<port>` is the loopback port of a `kubectl port-forward` that the row opens.

| # | Command | Expected |
| --- | --- | --- |
| 0 | `free -m` and `docker ps --format '{{.Names}}'` | `MemAvailable` at least the 10,880 MiB of the `ci` preset plus 1,024 MiB, else the run stops and records the figure without touching other containers |
| 1 | `uv sync --frozen` then `uv lock --check` | exit 0 for both, lock unchanged |
| 2 | `for m in wdf_workflow.gen wdf_runtime.gen wdf_events.schemagen wdf_jobdoc.schemagen wdf_artifactclient.gen; do python -m $m --check \|\| echo "DRIFT $m"; done` | no output |
| 3 | `for c in activity temporal; do for f in contracts/$c/vectors/*/valid/*; do contracts/$c/check.sh "$f"; done; done`, the same over every file under `contracts/{activity,temporal}/vectors/*/invalid/` | exit 0 for each valid file, exit 1 with the `reason:` of its directory for each invalid one |
| 4 | `scripts/check-contracts-map.sh`, `scripts/export-diagrams.sh --check`, `just versions --check` | exit 0 for each |
| 5 | `pytest packages/workflow -q -rs`, then `pytest packages/workflow -q -rs -m integration` | all pass with no `SKIPPED` line |
| 6 | `pytest packages/workflow -v -k "lost_completion_job_finishes or zombie_fenced or schedule_to_start_replaces or scheduler_down_beyond_task_timeout or deterministic_class_not_retried or infra_cap_exhausted or operator_interrupt_free or drain_then_pause or resume_from_checkpoint or resume_compile_failure_stays_paused or cache_hit_skips or cache_error_not_miss or build_info_key_mismatch_refused or aggregate_waits_for_all or allow_partial_partial_status or aggregate_only_job or cancel_in_flight or terminate_hard_stop or signal_dedup or stage_contract_workflow or failure_table_covers"` | each named test is listed `PASSED` |
| 7 | `pytest packages/temporal packages/workflow -v -k replay` | all pass, including the negative replay |
| 8 | `pytest packages/workflow -v -m slow -k cache_key_matches_build_info` | `PASSED` |
| 9 | `just up`, then `scripts/tilt/run.sh ci` | exit 0 with `temporaldb`, `temporal`, `appdb`, `objectstore` and `workflow-worker` healthy, and the bootstrap job completed |
| 10 | `python -m wdf_workflow.harness describe --address 127.0.0.1:<port>` | namespace `wdf`, retention `24h`, the five search attributes |
| 11 | `python -m wdf_workflow.harness smoke --address 127.0.0.1:<port> --jobs 2000`, then the history service's `cache_usage{cache_type="mutablestate"}` and each Temporal pod's `memory.events` | `finished: 2000`, `failed: 0`, `cache_usage` at most 1,000, `max` unchanged |
| 12 | `python -m wdf_workflow.harness restart --address 127.0.0.1:<port> --jobs 200 --target history,workflow-worker` | `finished: 200`, `failed: 0`, and `infra_retries` above 0 |
| 13 | `python -m wdf_workflow.harness rollout --address 127.0.0.1:<port>` | the job open before the rollout finishes on the old build id, a new job runs on the new one, and the old build is reported drained |
| 14 | `just doctor`, then `just down` and `just nuke` | exit 0 for each, and nothing of the run is left |
| 15 | `just ci` | exit 0 |
| 16 | the `ci` workflow on `main` | one green run, its URL recorded in Closure |

Not provable by this epic: the real placement service, pool table and OOM escalation (#9), real pool workers with the runner, the warm-up, exit 137 with node events and heartbeats from a guest (#10), the checkpoint format, segments, continue-as-new and kill-and-resume (#18), the admission start call and slot release (#8), signals through the control API and its outbox (#24), dashboards and alerts (#13), the standard load of 100,000 jobs at 1 GiB on the deployed Temporal (#16), engine and database outages longer than the heartbeat timeout (#15), a rollout with an old build kept for a month (#16), FIFO order across the partitions of a pool queue, and the aggregate over 100,000 results.

## 8. Parallelism and cost

- Maximum parallel workers: 3 in the first phase (0.1, S9, S10), 1 for 0.2, then 5 (P.1, P.2, A.1, D.1, M.1), then 4 (W.1, A.2, M.2, D.2), then 2 (W.2 and V.2, then W.3 and V.3), then 1 for W.4, W.5, V.1 and the exit test.
- Host constraints: T1 work holds at most two containers across the host, and W tests hold one (the dev server).
  The cluster is single-owner, so S10, M.1, M.2, D.2 and the T2 halves of V.2, V.3 and the exit test queue for the lock, and `ci` needs 10,880 MiB of the 20 GiB host beside other containers.
- CI cost, estimates: no new workflow, two to three dev server containers per `just itest` session, and the replay corpus adds seconds.
- Estimates, not measurements, in worker-days: lane 0 (4: 0.1 2.5, 0.2 1.5), S9 (1.5), S10 (2), M.1 (2.5), M.2 (1.5), P.1 (2.5), P.2 (2), A.1 (2.5), A.2 (3), W.1 (3), W.2 (3), W.3 (2), W.4 (2.5), W.5 (3.5), D.1 (2), D.2 (2), V.1 (2), V.2 (2.5), V.3 (2.5), exit test (2).
  That is about 48.5 worker-days of effort and about 24.5 days elapsed along lane 0, P.1, W.1 to W.5, V.1 and the exit test (4 + 2.5 + 3 + 3 + 2 + 2.5 + 3.5 + 2 + 2), with S10 failing into the fallback of decision 20, the chart's per-service footprint, the SDK's versioning calls on 1.35.0 and cluster lock contention as the main sources of slip.

## 9. Closure (filled at close)

Closure addenda, one dated line each, added after close when a `revisit` child issue is filed against this epic:

- none yet
