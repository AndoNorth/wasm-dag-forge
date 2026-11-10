# Epic #6: Workflow engine (Temporal) - breakdown

Status: draft
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/6
Architecture note: docs/architecture/12-dag-and-workflow.drawio
Verifier: none, per decision 1.
Depends on (closed epics): Dev environment (#1), Fixed-resource cluster (#2), Job event envelope (#4), DAG configuration schema and validator (#5), Durable state and artifact storage (#3), Artifact passing (#12), Workload toolchain (#11)

Evidence marks: `[read]` means read on `main` at `a1a62d4` on 2026-11-10 while preparing this spec, and nothing was executed beyond read-only commands.
`[looked up]` means read on 2026-11-10 from the upstream release pages, the chart's files at its tag, or the installed `temporalio` 1.35.0 source in `.venv`, and nothing was run against a server.
The epic issue body was read on 2026-11-10.
Method: [docs/delivery/method.md](../method.md).
Model: [docs/delivery/epics/07-workload-toolchain.md](07-workload-toolchain.md) and [06-artifact-passing.md](06-artifact-passing.md), whose structure this spec follows.
Tiers: T0 is host Python with no runtime, T1 is containers started through `wdf_itest` (the Temporal dev server of the pinned CLI image, Postgres and SeaweedFS, at most two containers across the host), and T2 is the cluster from `just up` on preset `ci`.
A test that needs a container carries the `integration` marker and runs in `just itest` and in every lane's `just ci`.
Command prefixes `nix develop -c` are omitted from every command of this spec and every command takes them, and every Python command also takes `uv run --frozen`.
T2 commands run under `flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 ...` as the cluster epic says.

## 0. Decisions

Section 0.1 lists what is already decided and carried, 0.2 the questions, all answered, 0.3 the defaults that the lanes build on, and 0.4 the proposals that lane 0 and the issues pin.
A change to a recorded decision changes only what its Applied in column names, and a change to a default only what its Changes column names, where an entry is an issue, a section of this spec or another decision or default.
The last column of 0.4 records the epic item that a proposal answers.
"0.1 (issue)" and "0.2 (issue)" name the lane 0 issues of section 4, and a bare 0.1 to 0.4 names a part of this section.

### 0.1 Recorded decisions

Everything in the epic body stands except where merged work has overtaken it, which this table and section 1 list.

| # | Decision | Source | Applied in |
| --- | --- | --- | --- |
| 1 | No verifier skill, no feature files and no verify recipes for this epic, and the `verify-execution` row of the method is dropped for #6. Every issue names its tests and the exit test is a command checklist | the standing rule applied by the envelope, validator, state, artifacts and toolchain epics (`05-state-storage.md:27`, `06-artifact-passing.md:36`, `07-workload-toolchain.md:45`) | sections 3, 4, 6 |
| 2 | Python models are the source of anything with a schema, and the exported JSON Schemas are checked in with a drift test | envelope epic decision 5, carried by every later epic | 0.1 (issue), 0.4 |
| 3 | Runtime libraries read contract files through package data and never by repository path, and every package has a wheel test | state epic decision 11, carried | 0.1 (issue), 0.2 (issue), every package issue |
| 4 | The workflow input is the accepted event plus the `job.params` block, which admission reads from the row and passes as a second start argument, and the workflow never reads Postgres to decide lifecycle. This replaces the #4 Interfaces row "the workflow input is the accepted event" | envelope question 3 (`03-envelope.md:67`), `wdf_events.models.JobParams`, the stand-in `StandInJob.run(accepted, params)` [read] | W.1, A.1 |
| 5 | A partial run ends in status `partial` with the event `dev.wdf.job.partial.v1`, whose data lists the tasks that did not run to completion. This answers the epic's open question 3 | envelope question 2 (`03-envelope.md:66`), `contracts/status/` status v1 | P.1, W.3 |
| 6 | Job and task events leave through one batched local activity behind one emit point, with deterministic ids and the time of the fact, and the workflow projects `running`, `paused` and the terminal statuses through the guarded `transition` under role `wdf_workflow`. Temporal has its own plain Postgres, delivered as `deploy/temporaldb/`. This answers open questions 10 and 11 | `05-state-storage.md:159,176`, `contracts/state/README.md:159`, `deploy/temporaldb/module.yaml` [read] | A.1, M.1 |
| 7 | A manual pause cancels a running checkpoint-capable simulate task, the worker takes a final checkpoint and exits with class `operator_interrupt`, which consumes no budget, and the task runs again after the resume under the next resume counter (decision 23), so attempt keys do not collide. Any other running task runs to completion, and the job projects `paused` only when nothing runs. The server-side activity pause is not used. This answers open question 2 | #24 body "Manual pause of a running task (decided, with #6, #10 and #18)", #10 and #18 bodies [read] | W.4, 0.1 (issue) |
| 8 | Admission learns of a pause from the job row that the workflow projects, and a granted resume is signalled to the paused workflow by admission. This answers open question 1 | #8 and #24 Interfaces rows, #18 body [read] | W.5, 0.1 (issue) |
| 9 | The compile cache key is `wdf_keys.compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version, entry)`, five inputs, computed nowhere else, with the two toolchain inputs from `wdf_runtime.pins`. This replaces the "cache key" wording of the epic body | events v3 (`contracts/events/README.md`, Keys), toolchain decision 5 [read] | A.2 |
| 10 | The workflow looks up the cache before placing a compile task, links a hit with `cache.use`, records the entry with `cache.record` after the commit of the compile attempt, refuses to record an entry whose `build-info.json` carries another `cache_key` than the one it computed, treats a lookup error as an error and never a miss, and treats `ARTIFACT_LOST` of a cache-sourced component as an invalidation and a rebuild. Before it invalidates, it looks the key up again and invalidates only when the live entry still names the lost component, because `invalidate_cache_entry` retires whatever entry is live for the key and another job may already have rebuilt it. The compile worker writes no cache entry, which supersedes the #10 Interfaces row for #12 | artifacts decision 18 and rules row for #6 (`06-artifact-passing.md:53,226`), toolchain decision 6 and rules row for #6 (`07-workload-toolchain.md:35,109`), `packages/artifactindex/src/wdf_artifactindex/index.py:253-265` [read] | A.2, W.1 |
| 11 | The workflow commits only the accepted attempt with a client of the `workflow` store role, retries the attempt when the commit raises `OUTPUT_UPLOAD_FAILED`, builds the aggregate index in an activity before the aggregate task from the recorded results in task id order with `build_index` and `put_index`, commits the index reference inside the same activity (decision 24), and chooses the previous checkpoint after `CHECKPOINT_CORRUPT` | artifacts rules row for #6 (`06-artifact-passing.md:226`), artifacts D7 (`:91`) [read] | A.2, P.2, W.2, W.3, W.5 |
| 12 | The workflow passes the bound `params` of each task to the worker in the activity payload that this epic pins, because the task request carries none, and the worker writes them as `/in/input.json` | toolchain 0.4 Input file row (`07-workload-toolchain.md:86`), `TaskRequestData` [read] | 0.1 (issue), W.1 |
| 13 | The workflow maps every class of `contracts/workload/exit-classes.yaml` and every code of `contracts/artifact/failures.yaml` into its failure class table and never retries a deterministic class. Aggregate waits for every input unless the task sets `allow_partial` | toolchain rules row for #6, artifacts rules row for #6 [read] | 0.1 (issue), P.1, P.2, W.3 |
| 14 | No submit-time resource check: the runtime library refuses a task whose guest cap is below `GUEST_MIN_PAGES` (209 pages) as a deterministic `guest_memory_cap`, which the workflow does not retry. The first run of a component on a pod is a separate warm-up of about 30 s at 250m before the task's guest runs, so the static pool table gives the simulate pool a heartbeat timeout and a start-to-close margin above it | toolchain decisions 20, 21 and 24 (`07-workload-toolchain.md:49,50,53`) [read] | 0.1 (issue) |
| 15 | `paced_sim`, the cooperative stop marker, the checkpoint format and the kill-and-resume test belong to the checkpoint epic's pass (#18), so this epic tests pause and resume with fake checkpoint references | toolchain decision 13 [read] | W.5 |
| 16 | The reaper's Temporal Schedule worker is deployed with this epic's worker service, on a client of namespace `wdf`, and the bootstrap creates the Schedule in `wdf` with `ensure_schedule`, because the reaper module's own entry points connect to the default namespace. A test holds the reaper's Schedule id, workflow, activity and task queue (`wdf-artifact-reaper`) equal to the naming contract | artifacts closure "What moved" (`06-artifact-passing.md:513,516`), `packages/artifactreaper/src/wdf_artifactreaper/schedule.py:35-39,170-190` [read] | 0.2 (issue), M.2, D.1 |
| 17 | Real stages call `run_stage_contract`, so the real workflow passes `run_stage_contract("workflow", ...)`, supplies the event sequence after a resume and calls `stage_span` itself. The status move matrix may be extended only by a contract-change PR | envelope closure follow-ups (`03-envelope.md:351-356`) [read] | W.1, V.1 |
| 18 | The two dynamic-config keys `history.hostLevelCacheMaxSize: 1000` and `history.eventsCacheMaxSizeBytes: 32768`, Temporal at 1 CPU and 1 GiB, its Postgres at 1 GiB and at least 4 GiB of disk, and 512 shards fixed at install | the epic body, S3b (`docs/delivery/spikes/temporal.md`) [read] | M.1, S10 |
| 19 | The pause reason (`operator`, `retries-exhausted`, `resume-compile-failed`) and the tasks that never ran are fields of the status query, and no contract changes: `job.paused` keeps no data, the job row gets no reason column, and `task.skipped` keeps only `compile-cache-hit`. The event history still holds the cause of every pause through the preceding `job.pause.requested` or `task.retries.exhausted` event, the `job.failed` reason text and the `skipped_tasks` of `job.partial` (question Q1, answer (a)) | answer of 2026-11-10 | 0.4, A.1, W.4, W.5 |
| 20 | If spike S10 shows that the official chart's four service pods do not fit Temporal's 1,024 MiB on the infra node with the two cache keys of decision 18 active, one pod runs every service in one `temporal-server` process from the pinned server image, the shape S3b measured, with the same keys and limits. The infra node stays at 5,120 MiB, and a split-service install stays possible on a larger preset (question Q2, answer (a)) | answer of 2026-11-10 | S10, M.1, M.2, section 7 |
| 21 | The cluster (T2) proves the Temporal module, the bootstrap and the deployed workflow worker, driven by the harness with fake pool workers on the host through `kubectl port-forward` bound to 127.0.0.1, because T1 holds at most two containers and the cluster is the only place where real activities, Postgres, the store and Temporal meet in this epic (question Q3, answer (a)) | answer of 2026-11-10 | D.2, V.2, V.3, section 7 |
| 22 | `oom_killed` of the simulate pool consumes the infrastructure budget, because a kill while the guest is below its `-W max-memory-size` cap is a platform sizing fault, and `compile_oom` and `aggregate_oom` consume the guest budget with #9's single escalation when a larger memory class exists (question Q4, answer (b)) | answer of 2026-11-10 | 0.4, P.2 |
| 23 | The job pauses when a task whose plan `checkpoint` flag is set exhausts a budget, and a resume re-runs every task that has not succeeded (paused and failed alike, while a compile in state `skipped` counts as succeeded) with fresh budgets and the next resume counter, from the last good checkpoint where one exists (question Q5, answer (a)) | answer of 2026-11-10 | P.1, W.5 |
| 24 | Each control activity runs under the database role of its writer: the control worker holds a login in `wdf_workflow` for `emit`, and a second login in `wdf_artifacts` for `commit`, `index_checkpoint`, the cache activities and `build_index`, which commits the index it puts, as the dispatch API does, because `wdf_workflow` has no grant on `artifact` or `artifact_use`. The reaper worker in the same process holds a login in `wdf_reaper` and the `reaper` store identity, which it reads from `WDF_REAPER_DSN`, `WDF_REAPER_ENDPOINT` and `WDF_REAPER_CREDENTIALS`. The group roles cannot log in, so the workflow module creates the three service logins | `contracts/state/README.md:160,165`, `packages/stateops/src/wdf_stateops/migrations/R__roles.sql:5,28-32,57-63`, `schedule.py:54-56` [read] | D12, A.1, A.2, D.1, D.2 |

### 0.2 Questions

All questions are answered: the recommended option of each of Q1 to Q5 of the draft was taken on 2026-11-10, recorded as decisions 19 to 23 in section 0.1.

### 0.3 Defaults the lanes build on

These carry the remaining open questions of the epic and the choices found while reconciling it.
Numbers marked as estimates are fixed by #16, except the values of pool table v0 (the first schedule-to-start timeout of D5, `heartbeat_s` and the start-to-close margin), which #9 replaces.

| # | Topic | Default | Reason, and the alternative | Changes |
| --- | --- | --- | --- | --- |
| D1 | Plan loading (open question 9) | An activity `load_plan` reads the manifest of the accepted event with the `workflow` store role, checks its hash, reads it with `wdf_plan.read_plan` and binds the params with `wdf_plan.bind`, and the bound plan (at most 256 KiB of canonical bytes, `max_plan_bytes`) is recorded in history with the `max_history_events` of its limits profile. A resume with a patched manifest loads it the same way | Decision 4 keeps the plan out of the start input, and the manifest is needed for a patched resume either way | A.1, W.1 |
| D2 | How the checkpoint reference reaches the workflow (open question 8) | A failure the worker reports carries the latest checkpoint reference in its failure details, and a heartbeat timeout is read from the last heartbeat details, so a lost worker still hands over its last reference. The workflow keeps the last two references per task, newest first, which is the keep count of the checkpoint client (`MIN_KEEP`). `CHECKPOINT_CORRUPT` drops the head and the next attempt starts from the one behind it, and when no reference is left the failure becomes `ARTIFACT_LOST`, as the artifact contract's taxonomy says. A task that never wrote a checkpoint starts from its beginning. The workflow indexes each reference it receives with the control activity `index_checkpoint(reference, job_id, task, attempt)` on the control queue, under the `wdf_artifacts` login of decision 24, which refuses a reference whose producer is not that job, task and attempt, as `commit` does, and calls `wdf_artifactindex.index_reference` with no store access. Then `emit` writes `task.checkpoint.committed` with `sequence` 0 for that attempt, an event the stage contract gives to the workflow (`contracts/stages/stages.yaml:55`). A checkpoint that a worker writes and replaces within one attempt never reaches the workflow and is removed with its job by the reaper's terminal pass, and indexing within a long attempt comes with #18's segments (D7) | A dead worker cannot report, so heartbeat details are required, and failure details avoid a race with the last heartbeat. One reference is not enough, because the one that fails verification is the latest, and `wdf_workflow` cannot query the index for an older one. The reaper prunes a running job only through indexed checkpoint rows (`packages/artifactreaper/src/wdf_artifactreaper/reap.py:197-231`), and `commit` cannot index a checkpoint, because its HEAD reads `wdf-checkpoints`, on which the `workflow` store role has no action (`contracts/store/roles.yaml:26-29`) | 0.4, A.2, W.2, W.5 |
| D3 | Infrastructure budget | 10 attempts per task, reset at each resume, with backoff 10 s doubling to 10 min (estimates). Exhausting it follows the same rule as the guest budget (D9). Two consecutive `oom_killed` of one task stop that retry: the task takes `escalate-once` where the placer offers a larger class, and otherwise its infrastructure budget counts as exhausted | A weekly rollout and a node loss cost one each, and a permanently broken pool stops within about an hour. A simulate pod sized too small for its task is killed at the same point on every attempt, and ten attempts of a task with a run timeout of hours would outlast the job | 0.4, P.2 |
| D4 | Timeouts | The guest's own run timeout (`timeout`, `compile_timeout`, `aggregate_timeout`) consumes the guest budget. A Temporal start-to-close or heartbeat timeout consumes the infrastructure budget | The run timeout is the author's declared limit, and a Temporal timeout means the worker did not report | 0.4, P.2 |
| D5 | Schedule-to-start (open question 6) | The first schedule-to-start timeout is 1,800 s, doubling at each re-schedule to a ceiling of 12 h, with no count cap, an alert metric at each timeout and a new `place` call before scheduling again (estimates). Over the 7-day maximum job duration this bounds a task to about 20 re-schedules and about 120 history events | The epic fixes that a task is never dropped, and a re-schedule moves the task to the tail of its FIFO queue, so short timeouts would starve it under a deep backlog | 0.4, P.2, W.2 |
| D6 | Versioning behaviour (open question 7) | `JobWorkflow` is declared `@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)` and the workflow worker runs with `WorkerDeploymentConfig(version=WorkerDeploymentVersion("wdf-workflow-worker", <build id>), use_worker_versioning=True)`, so every job finishes on the build that started it. `wdf-workflow` is the only versioned queue, and the control, reaper and pool workers pass no `deployment_config` and stay unversioned. SDK 1.35.0 has no client method for worker deployments, so the current version is set with the raw `set_worker_deployment_current_version` call at every deploy including the first, because a deployment with no current version gives new workflows only to unversioned pollers. The pinned-bug override is the raw `update_workflow_execution_options` call with a `PinnedVersioningOverride` to the fixed build, whose change is guarded by `workflow.patched` because that build replays the whole history, or the fallback S9 records on a fail | S3 showed an unversioned activity worker serving both builds, and auto-upgrade of short jobs would need every change to be replay-safe. The SDK surface is `[looked up]` in the installed 1.35.0: `worker/_worker.py:143`, `bridge/services_generated.py:498,1686,2210`, `common.py:1228,1286` | V.2, S9 |
| D7 | Continue-as-new and segments | None in this pass. Segments of 1 to 6 h and continue-as-new at a quiescent point are built with #18's pass, which takes over the workflow files it changes through its own lanes, and the epic's "continue-as-new readiness spike" moves there | The epic body says no continue-as-new in v1, the plan limits bound history, and only the 30-day segmented job needs it | none |
| D8 | Drain | After a budget is exhausted, every task that does not depend on the exhausted task keeps running, including tasks that become ready, and the job pauses or ends when nothing can run. After a manual pause no new task is placed, a running checkpoint-capable simulate task is cancelled and takes a final checkpoint (`operator_interrupt`), and every other running task runs to completion (decision 7). During a manual pause, a cancel that resolves before its attempt started, which the SDK reports as cancelled with no failure, and a `schedule_to_start_timeout` are not failures: neither reaches `decide`, consumes a budget or re-places, and the task returns to `ready` to be placed after the resume, so a manual pause waits at most the current schedule-to-start timeout of a task that never started | The epic's "independent branches drain" for exhaustion, and #24's "places no new task" for an operator pause. A queued activity has no worker to take a final checkpoint, and the installed sdk-core resolves a cancel of an activity that was scheduled but not started as cancelled without waiting (`temporalio/bridge/sdk-core/crates/sdk-core/src/worker/workflow/machines/activity_state_machine.rs:55,68,70-71` [looked up]) | P.1, W.4 |
| D9 | Pause or fail | The rule keys on the plan's `checkpoint` flag of the task that exhausted a budget, not on the job class and not on whether a checkpoint was written. A checkpoint-capable task that wrote none resumes from its start. A job with a paused chain and a failed chain pauses, and the status query lists both. One exception: a compile that runs for a resume (W.5) and exhausts its budget pauses the job again with reason `resume-compile-failed`, whatever the flag, and P.1's pause-or-fail function takes that case as an input | #18's open question "keyed on has written a checkpoint, or on the long job class", answered by the plan flag, which the workflow already reads. A compile task has no checkpoint flag, and failing a job that an operator just tried to fix would lose its checkpoints | P.1, W.5 |
| D10 | Temporal Web UI (open question 12) | Not deployed and not part of acceptance. The status query shows the state of a job, and the harness `describe` command shows the namespace, the search attributes and the Schedule | It costs memory on the infra node and no acceptance step reads it | M.1 |
| D11 | Namespace and retention | Namespace `wdf`, retention 1 day (estimate) | S3b's 100,000-job run stored 3,507 MiB at 1 day, and the `temporaldb` volume is 5,120 MiB. Dedup stays at submit (D4 of the epic) | M.2 |
| D12 | Where control activities run | `place` and `emit` are local activities (decided), each retried with the policy of D15 (no attempt limit, backoff 1 s doubling to 600 s, an alert metric), so a database outage stalls a job and fails nothing. `load_plan`, the cache activities, `commit`, `index_checkpoint` and `build_index` are regular activities on an unversioned control queue `wdf-control-<build id>`, one per build, served only by the activity worker inside that build's workflow worker service with the logins of decision 24 and the `workflow` and `indexer` store credentials, so a workflow pinned to a build runs its control activities on that build's code and toolchain pins. Each of these regular activities has the retry policy that `naming.yaml` pins (at most 5 attempts, backoff 1 s to 60 s, estimates) and raises every typed error of the artifact client, `CommitRefused`, `IllegalTransition` and `JobNotFound` as an `ApplicationError` whose `type` is the code and which is non-retryable when `failures.yaml` or the state library says so, so the workflow sees `OUTPUT_UPLOAD_FAILED` and retries the attempt. A non-retryable `IllegalTransition` or `JobNotFound` from `emit` is a defect: the workflow re-raises it as a `RuntimeError`, which the SDK treats as a failed workflow task and not a failed workflow, so Temporal retries the task until a fixed build is deployed | An index over many results can outlast a workflow task timeout, and the pool workers hold no database access. Under Temporal's default policy a plain exception retries without limit, and the workflow would never see the error. On one shared control queue, a workflow pinned to one build would run control activities of the other build during a rollout (D21), whose argument shapes or toolchain pins it may not match | 0.4, A.1, A.2, W.1, D.1 |
| D13 | Cache lookup | One activity `cache_lookup(source, entry, job_id)` reads the source bundle, takes its `BundleInfo` with `wdf_compile.bundle.info` (the function `wdf_compile.build` uses), computes the key with the pins of `wdf_runtime.pins`, memoises the key in the process by (source hash, entry), looks it up, links a hit with `cache.use`, and returns the key and the hit. A bundle with no `uv.lock`, or whose modules lack the entry, has no key: the lookup says so, the task is placed uncached, and the worker fails it as `compile_failed`, as `wdf_compile.build` refuses it | The plan holds the source reference but not the lockfile hash, 100,000 jobs of one batch share one key, and one activity instead of three keeps the compile task's history events inside `history_events_per_task` | 0.4, A.2 |
| D14 | `task.started` | The activity result and the failure details carry the worker's `started_at`, and the workflow emits `task.started` with that time when the attempt ends. An attempt that ends in a heartbeat timeout with no details emits none | The workflow cannot see an activity start, the worker emits no event (stages v1), and `time` is the time of the fact | 0.4, W.1 |
| D15 | Scheduler down | The `place` local activity retries with backoff from 1 s doubling to a ceiling of 600 s (an estimate) and no attempt limit, with an alert metric. Once a backoff is above the SDK's local retry threshold of 60 s, the retry waits on a workflow timer instead of holding the workflow task open, so a scheduler outage stalls placement and fails a job only if the timer events of the outage exhaust its history budget (D16). W.2 tests an outage longer than the workflow task timeout and records the history events that one hour of outage adds to a job | The epic records behaviour beyond a workflow task timeout as unverified, and a long local activity adds workflow task events. With a ceiling at or below the threshold, the SDK retries in process and heartbeats the workflow task about every 8 s, three events each, about 1,350 events an hour, which ends a job at the 10,000 of `max_history_events` (`contracts/job/limits.yaml:16`) within about 7 hours (`temporalio/bridge/sdk-core/crates/sdk-core/src/worker/activities/local_activities.rs:776-777`, `protosext/mod.rs:417-420`, `worker/workflow/mod.rs:119` [looked up]) | 0.4, W.2 |
| D16 | History budget (detail issue 12) | The workflow reports its history length as a metric at each activation. It enforces the `max_history_events` that `load_plan` returns: at that length it places no new task, lets running tasks finish, and ends the job `failed` with the reason `history-budget: <length>`. W.2 measures events per job, per task and per retry against `history_events_per_job` (10), `_per_task` (8) and `_per_retry` (6) of job v2, and a measured figure above an estimate is raised then as a contract-change question on job v2. The alert rule on the metric is #13's | The plan estimate counts neither the infrastructure budget, re-schedules, resumes nor placement waits, and without continue-as-new (D7) a resume cannot shrink a history, so a pause would only come back to the same limit | 0.4, W.1, W.2 |
| D17 | Event dedup at the emit point | `emit` reads the stored `event_id`s of its batch first (the `select` on `event_id` granted to `wdf_workflow`) and inserts only the new ones, so an id is written once whatever its time | `insert_events` keys a row on the id and the time (`packages/eventstore/src/wdf_eventstore/store.py:20,54`), and a workflow task that is retried after `emit` committed rebuilds the batch with the same ids and a later `workflow.now()` | A.1 |
| D18 | A `Lost` answer of `transition` | `Lost(s)` with `s` equal to the target counts as moved. `Lost(s)` with `s` a terminal status means the job ended outside the workflow: the workflow cancels its running tasks as for `cancel`, writes no further event and completes. `Lost('resume-pending')` on a move from `paused` to `cancelled` or `terminated` is tried once more from `resume-pending`. Any other `Lost` rolls back the batch's events, and the workflow logs it, counts it in a metric, places no new task and sends the same batch again with the backoff of D15 until the move succeeds or one of the cases above applies, so the row and the workflow never stay apart | `transition` returns `Lost` with the current status and not who moved the row (`packages/jobstate/src/wdf_jobstate/jobs.py:157-186`), and control moves `paused` to `resume-pending` beside the workflow. Keeping the workflow's own expected status after a `Lost` would lose every later move, and the row would never reach a terminal status. Before the workflow's first move, admission claims the row (`contracts/state/README.md:158`) and control may end a job that never started with a row-only move (`:157`), which the terminal case covers, and the #8 rules row has admission commit `admitted` before the start, so a lasting `Lost` is a defect that the metric shows | A.1, W.4 |
| D19 | Control signal order | `pause` and `resume` share one last-applied `control_seq`, and a `pause` or `resume` whose `control_seq` is not above it is ignored. `cancel` and `terminate` are applied whenever they arrive unless the job has ended, and a repeat is a no-op. Admission sends `resume` only while the row's `requested_action` is `resume`, with the row's `control_seq` | `request_action` takes the next sequence for each new action (`jobs.py:189-210`), so a resume read from the row after a later cancel would carry the cancel's sequence and hide it | 0.4, W.4, W.5 |
| D20 | Cancelling a pool activity | Pool activities start with `cancellation_type=WAIT_CANCELLATION_COMPLETED`, and a task leaves `running` only when its activity returns | Under the SDK default `TRY_CANCEL` the workflow sees the cancel at once, loses the `operator_interrupt` failure with the final checkpoint reference, and projects `paused` while the worker still uploads | 0.4, W.1, W.4 |
| D21 | Two builds during a rollout | Each build runs as its own Deployment `workflow-worker-<build id>`, and the old one is deleted only when V.2 reports it drained. A paused job pinned to an old build is moved to the current build by the override of D6 before the old build is deleted | A rolling update removes the old pod and strands its pinned workflows, and a paused job keeps its build busy for as long as it waits | D.2, V.2 |
| D22 | Temporal's CPU in `wdf-infra` | The `ci` quota of `wdf-infra` has `limits.cpu` 1,995m, derived from the infra node's 2 CPUs, and the modules on `main` already take 1,990m (`appdb` 1,000m, `temporaldb` 450m, `objectstore` 440m, `mirror` 50m and the infra dummy 50m, while `appdb-migrate` and `objectstore-bootstrap` run on the host and take no quota, `deploy/appdb/migrate/Tiltfile.module:2`, `deploy/objectstore/bootstrap/Tiltfile.module:2`), so the 1 CPU of decision 18 does not fit. A `revisit` child of #2 raises the `ci` infra node from 2 to 3 CPUs, which `scripts/cluster/namespaces.sh` turns into a quota of 3,045m, and merges before S10 runs. The preset then counts 8 CPUs, which equals the 8 of the 20 GiB WSL host | The alternative is to lower the CPU limits of the closed state epic's modules, of which `appdb` at 1 CPU is the largest, which changes sizing that S8 measured. Decision 18 itself is not changed | S10, M.1, section 2 |
| D23 | Helm values and lint | The chart values of M.1 are one `deploy/temporal/values.json`, read by `Tiltfile.module` and by the render test, and the dynamic config is in it. No YAML file other than `module.yaml` and Kubernetes manifests is added under `deploy/temporal/` or `deploy/workflow/` | `scripts/lint.sh` runs `kubeconform -strict` on every YAML file under `deploy/` except module manifests, presets, tilt and host ports, so a values file in YAML fails it, and `scripts/` is not this epic's to change | M.1 |
| D24 | Replay corpus of `JobWorkflow` | The recorded histories live in `packages/workflow/corpus/` with their own `owners.yaml`, recorded and replayed with `wdf_temporal.corpus` (`replay_corpus(workflows, root)`), and the corpus of `packages/temporal/` is not touched | The whole-corpus test of `packages/temporal` replays every history under its root with `StandInJob` only (`packages/temporal/tests/test_replay.py:13`), so a `JobWorkflow` history there fails it | V.1, section 2 |
| D25 | CI for the new modules | No new CI workflow and no change to `.github/workflows/`. The `state-modules` workflow deploys every `ci` module on a push to `main`, and its pull request path filter does not list `deploy/temporal/**`, `deploy/workflow/**` or `packages/workflow/**`, so M.1, M.2 and D.2 each run `scripts/tilt/run.sh ci` themselves and paste the output, and the exit test records a green `state-modules` run on `main` | The artifacts epic recorded the same gap in its decision 15, and a filter change touches every lane's CI cost | M.1, M.2, D.2, section 7 |

### 0.4 Proposals the lanes pin

After lane 0 merges, `contracts/activity/README.md`, `contracts/temporal/README.md` and their files are the source and this section is only a pointer.
The tables after this one give every field, row and loader reason that 0.1 (issue) pins.

| Item | Proposal | Epic item |
| --- | --- | --- |
| Contract directories | Two contracts, owner #6, version 1 each: `contracts/activity/` (contract 4 of the method) and `contracts/temporal/` (contract 5, whose `pools.yaml` the placement epic #9 takes over at its real values). Each has `VERSION`, `README.md`, `check.sh`, generated schemas and vectors under `vectors/<name>/{valid,invalid/<reason>}/`. Models live in `wdf_workflow.models`. Loader `python -m wdf_workflow.contract --check FILE [--as NAME]` (exit 0 valid, 1 with `reason: <code>: <detail>` on stderr, 2 usage), generator `python -m wdf_workflow.gen [--check]` | method contracts 4 and 5 |
| Task input | `TaskInput`, the one argument of `run_task`: the request event, the task attempt, the resume counter, the batch id, the job seed, the bound params and the compile cache key | decisions 10 and 12 |
| Task result | `TaskResult`: the output references in the order of the staging contract, the log tail reference and `started_at` | completion, D14 |
| Failure details | A reported failure is an `ApplicationError` whose `type` is the failure class and whose details are `FailureDetails`: deterministic, reason, `started_at`, checkpoint and log tail references | D2, D14 |
| Heartbeat details | `HeartbeatDetails`: the guest progress or nulls, the latest checkpoint reference and `started_at` | D2, #4 and #10 Interfaces |
| Failure class table | `failure-classes.yaml`: one row per class of the five sources, with its deterministic flag, the budget it charges and its action | open question 5, detail issue 5, decisions 13 and 22, D3, D4 |
| Naming | `naming.yaml`: namespace, workflow, queues, activities with their retry policies, signals, query, deployment, search attributes and the reaper Schedule | detail issues 1, 2, the #8 start contract |
| Signals and query | `ControlSignal` for `cancel`, `terminate` and `pause`, `ResumeSignal` for `resume`, both ordered by D19, and `StatusQuery` for query `status` | detail issue 8, #24 Interfaces |
| Pool table v0 | `pools.yaml`, static: the `ci` slots, the first schedule-to-start timeout and its ceiling, the heartbeat timeout and the start-to-close margin per pool. All are estimates, and #9 replaces the file with real values and formulas | method contract 5, decision 14, D5 |
| Placer | `wdf_workflow.placement.Placer` with `place(request, escalate=False) -> Placement | Unschedulable`, `Placement(queue, memory_class, escalated, schedule_to_start_s, start_to_close_s, heartbeat_s)`, and `StaticPlacer` over pool table v0, which has one memory class per pool and so never escalates. `wdf_workflow.testing.placer_contract(placer)` is the conformance test that #9's client runs | detail issue 4, #9 Interfaces, decision 22 |
| Fake pool worker | `wdf_workflow.testing.FakePool`: an activity worker that serves `run_task` on a pool queue with scripted outcomes (succeed with outputs written to a store or kept local, fail with a class, heartbeat a checkpoint reference, report `operator_interrupt` with a final checkpoint on cancel, hang, die without completing, complete after its attempt was timed out). `wdf_workflow.testing.activity_contract(worker)` is the conformance test that #10's worker runs: it fails a worker whose failure `type` is not a row of the table, whose payload holds bytes instead of references, or which answers a cancel without `operator_interrupt` and a checkpoint | stub for #10 |
| Fake control activities | `wdf_workflow.testing.FakeControl`: in-memory `load_plan`, `cache_lookup`, `cache_record`, `cache_invalidate`, `commit`, `index_checkpoint`, `build_index` and `emit` under the names of `naming.yaml`, with scripted errors and `Lost` answers. `wdf_workflow.testing.control_activity_contract(impl)` runs against the fakes in lane W and against lane A's activities in A.1 and A.2, and checks argument and result shapes, the `ApplicationError` types of D12 and the `Lost` cases of D18 | stub for lane A |
| Service | `python -m wdf_workflow.service` runs the workflow worker (`JobWorkflow`, `PINNED`, build id from `WDF_BUILD_ID`), the control activity worker on `wdf-control-<build id>` (D12) and the reaper's Schedule worker in one process, with SDK Prometheus metrics, the workflow metrics of W.1 named by the instrumentation contract, the `wdf_temporal` converter and tracing interceptor, and a `SIGTERM` drain. Configuration comes from the environment only: the Temporal address and namespace, `WDF_BUILD_ID`, the two app database DSNs of decision 24, the store endpoint and the paths of the `workflow` and `indexer` credentials, and the three `WDF_REAPER_*` variables | detail issue 2 |
| Harness | `python -m wdf_workflow.harness {describe,smoke,restart,rollout}`, which seeds job rows and manifests, claims each row from `accepted` to `admitted` as admission would (the #8 rules row), starts workflows with the start options of the naming contract, runs `FakePool` workers on the host, and prints counters. At T1 it takes `--address HOST:PORT` only, serves the control queue with `FakeControl`, seeds nothing, and refuses `pause-resume` and `zombie` with exit 2, because both read the job row or the index and the dev server, Postgres and the store together are three containers. At T2 it takes `--kube-context NAME` instead, opens its own loopback port-forwards to `temporal`, `appdb` and `objectstore`, re-opens a forward whose pod restarted, and reads from the state directory the database superuser password and, from `objectstore.credentials.json` (`deploy/objectstore/README.md:46`), the `submit` identity, with which it seeds manifests and sources as the dispatch API would, and the `compile`, `simulate` and `aggregate` identities, one for the `FakePool` of each pool, as #10's workers will. `smoke`, `restart` and `rollout` take `--jobs N`, `restart` takes `--target POD,...` (the Temporal pods by name, the one pod under decision 20, or `workflow-worker`), and `--scenario` adds `pause-resume`, `zombie` and `worker-death` with `--deaths N`. A smoke job is a compile task, then four simulate tasks, then an aggregate, and every second job reuses one source so it hits the cache. In `pause-resume` the harness does what #24 and #8 would do: it calls `request_action` for the pause and sends `pause` with the row's `control_seq`, and once the job is paused it calls `request_action` for the resume, moves the row to `resume-pending` and sends `resume` with the row's `control_seq`. In `zombie` a `FakePool` completes after its attempt timed out, and in `worker-death` a `FakePool` dies `N` times. `describe` prints `namespace:`, `retention:`, one `search_attribute:` line per attribute with its type, and `schedule:`. The other commands print `finished:`, `failed:` and `infra_retries:`, plus `paused_resumed:` for `pause-resume`, `zombie_commits:` (the indexed outputs of an attempt that was not accepted) for `zombie`, and `old_build:`, `new_build:`, `open_job_build:` (the build that finished the job open before the rollout), `new_job_build:` and `drained:` for `rollout`. `rollout` builds and loads a second image of the same tree under the build id of `WDF_BUILD_ID`, deploys it beside the first (D21), makes it current with `wdf_workflow.versioning`, and deletes the old Deployment once it is drained. Reused by #15 and #16 | detail issue 14 |

#### Activity contract detail

Every model is in `wdf_workflow.models` and every schema is generated from it.
A payload is canonical JSON as `wdf_temporal.converter` writes a model, and a size bound counts those bytes.
A time is a string of `wdf_events.models.TIME_PATTERN`, and every reference reuses an existing model, so no new reference shape exists.
`LogTailReference` is `wdf_events.models.AttemptReference` narrowed to kind `log-tail`, as `ResultReference` narrows it.
Files: `VERSION`, `README.md`, `check.sh`, `failure-classes.yaml`, and the generated `task-input`, `task-result`, `failure-details`, `heartbeat` and `failure-classes` schemas.

Task input `TaskInput`, at most 262,144 bytes:

| Field | Type | Rule | Reuses |
| --- | --- | --- | --- |
| `request` | object | a `dev.wdf.task.requested.v1` event that `wdf_events.check_task_request` accepts, written as `wdf_events.to_bytes` writes it, with no `attempt` attribute, because the envelope refuses one on that type | `wdf_events.Event`, data `TaskRequestData` |
| `task_attempt` | integer | at least 1 | the task attempt of the events contract, Keys |
| `resume_counter` | integer | at least 0 | the second input of `wdf_keys.attempt_identity` |
| `batch_id` | string | a UUID | the `<batch>` segment of every attempt-scoped key |
| `seed` | integer | 0 to 2^53 - 1 | `JobParams.seed`, which bounds it below only, with the upper bound of `wdf_jobdoc.models.SEED_LIMIT` (`packages/jobdoc/src/wdf_jobdoc/models.py:37`), the `<seed>` segment of every attempt-scoped key |
| `params` | object | the `params` of the bound plan task for `request.data.task_type` | `wdf_jobdoc.models.CompileParams`, `wdf_runtime.models.Input`, `wdf_jobdoc.models.AggregateParams` with no placeholder |
| `cache_key` | string or null | 64 lower-case hex digits on a compile task with a key (D13), null otherwise | `wdf_keys.compile_cache_key` |

`request.data.inputs` holds no reference for compile, exactly one `component` for simulate, and exactly one `index` for aggregate (the index of `build_index`, or the submitter's `params.index`), and the worker reads the index from `inputs`.
The worker's producer is `ProducerRef(job=request.jobid, task=request.taskid, attempt=attempt_identity(task_attempt, resume_counter))`.

Task result `TaskResult`, at most 4,096 bytes: `outputs`, one or two references whose kinds are, in order, the declared outputs of one task type of `staging.yaml` (compile `component` and `build-info`, simulate `result`, aggregate `summary`); `log_tail`, a `LogTailReference`; and `started_at`, a time.

Failure details `FailureDetails`, at most 4,096 bytes, the details of an `ApplicationError` whose `type` is a row of `failure-classes.yaml` with source `workload`, `artifact` or `runner`:

| Field | Type | Rule | Reuses |
| --- | --- | --- | --- |
| `deterministic` | boolean | the row's value, or either value on a `from-error-file` row | `wdf_runtime.classify.Classified.deterministic` |
| `reason` | string | 1 to 1,024 characters, starting with the class and `: ` | `Classified.reason`, `ArtifactError.reason` |
| `started_at` | string | a time | D14 |
| `checkpoint` | object or null | the latest uploaded checkpoint of the attempt | `wdf_events.models.CheckpointReference` |
| `log_tail` | object or null | null only when the tail upload itself failed | `LogTailReference` |

Heartbeat details `HeartbeatDetails`, at most 2,048 bytes: `progress` (0 to 1), `step` (0 to `steps`) and `steps` (at least 1) from the `HEARTBEAT` line as `wdf_runtime.contract.heartbeat` parses it, all three null during staging, the warm-up of decision 14 or on a pool without guest heartbeats; `checkpoint`, a `CheckpointReference` or null; and `started_at`, a time.

Failure class table `failure-classes.yaml`, one row per class, keyed by class:

| Column | Values | Rule |
| --- | --- | --- |
| `class` | string | the class or code as its source file spells it |
| `source` | `workload`, `artifact`, `runner`, `temporal`, `placement` | the worker reports the first three, and the workflow decides `temporal` from the timeout type and `placement` from an `Unschedulable` verdict |
| `pool` | `compile`, `simulate`, `aggregate` or null | set exactly on a `workload` row, the pool `exit-classes.yaml` lists the class under |
| `deterministic` | `true`, `false`, `from-error-file` | a workload row copies `exit-classes.yaml`, where `from-error-file` takes the value of the failure details; an artifact row is `false` when its `attempt_class` is `retryable` or its retry rule is `fallback-to-previous-checkpoint`, and `true` otherwise |
| `budget` | `guest`, `infra`, `free`, `none` | `guest` and `infra` consume one attempt of that budget; `free` consumes nothing and the task runs again, or for `operator_interrupt` waits for the resume; `none` ends the task at once as an exhausted budget, so D9 decides pause or fail |
| `action` | null, `invalidate-cache-and-rebuild`, `previous-checkpoint`, `re-place`, `escalate-once` | an action replaces the budget rule for the occurrence it applies to |

| Action | Rule |
| --- | --- |
| `invalidate-cache-and-rebuild` | applies only when the lost object is the component of a compile cache hit of this job: the guarded invalidation of decision 10, a new compile task, then the consumer runs again consuming nothing, and a second loss in the same run ends the task |
| `previous-checkpoint` | the next attempt resumes from the checkpoint behind the head (D2), and when none is left the failure becomes `ARTIFACT_LOST` |
| `re-place` | a new `place` call with the doubled schedule-to-start timeout of D5 |
| `escalate-once` | the next attempt runs once in a larger memory class when the placer offers one, and pool table v0 offers none, so in this epic the guest budget alone applies |

The rows: every class of `contracts/workload/exit-classes.yaml` except `ok`, which is a success and has no row, every code of `contracts/artifact/failures.yaml`, and the runner, Temporal and placement classes.

| Class | Source | Pool | Deterministic | Budget | Action |
| --- | --- | --- | --- | --- | --- |
| `timeout` | workload | simulate | false | guest | |
| `guest_memory_cap` | workload | simulate | true | none | |
| `guest_failed` | workload | simulate | from-error-file | guest, or none when the details say deterministic | |
| `host_error` | workload | simulate | false | infra | |
| `oom_killed` | workload | simulate | false | infra (decision 22, and D3's two-in-a-row rule) | |
| `compile_failed` | workload | compile | true | none | |
| `compile_dependency_unavailable` | workload | compile | false | infra | |
| `compile_timeout` | workload | compile | false | guest | |
| `compile_oom` | workload | compile | false | guest | `escalate-once` |
| `aggregate_failed` | workload | aggregate | false | guest | |
| `aggregate_input_unavailable` | workload | aggregate | false | infra | |
| `aggregate_input_corrupt` | workload | aggregate | true | none | |
| `aggregate_input_refused` | workload | aggregate | true | none | |
| `aggregate_parse_failed` | workload | aggregate | true | none | |
| `aggregate_oom` | workload | aggregate | false | guest | `escalate-once` |
| `aggregate_timeout` | workload | aggregate | false | guest | |
| `STORE_UNAVAILABLE` | artifact | | false | infra | |
| `STORE_FORBIDDEN` | artifact | | true | none | |
| `ARTIFACT_NOT_FOUND` | artifact | | true | none | |
| `ARTIFACT_CORRUPT` | artifact | | true | none | |
| `ARTIFACT_LOST` | artifact | | true | none | `invalidate-cache-and-rebuild` |
| `OUTPUT_UPLOAD_FAILED` | artifact | | false | infra | |
| `OUTPUT_MISSING` | artifact | | true | none | |
| `CHECKPOINT_CORRUPT` | artifact | | false | infra | `previous-checkpoint` |
| `operator_interrupt` | runner | | false | free | |
| `worker_shutdown` | runner | | false | infra | |
| `heartbeat_timeout` | temporal | | false | infra | |
| `start_to_close_timeout` | temporal | | false | infra | |
| `schedule_to_start_timeout` | temporal | | false | free | `re-place` |
| `insufficient_memory` | placement | | true | none | |
| `insufficient_cpu` | placement | | true | none | |
| `no_matching_pool` | placement | | true | none | |

`ARTIFACT_NOT_FOUND` and `ARTIFACT_CORRUPT` escalate to `ARTIFACT_LOST` inside the client after its refetch, so a report of either means the refetch did not run, and the row ends the task.
`compile_dependency_unavailable` consumes the infrastructure budget because the wheelhouse mirror is the platform's, and `aggregate_failed` the guest budget because the aggregate library is the task's own code, as decision 22 treats `aggregate_oom`.

Activity loader reasons, the first failing check winning in this order:

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match its model, which includes an unknown budget or action, a row named `ok`, a step above steps and a partial progress triple |
| `too-large` | a payload is above its bound |
| `bad-request` | `check_task_request` refuses the request, or the request carries `attempt` |
| `params-type-mismatch` | `params` is not the shape of the request's task type |
| `cache-key-mismatch` | `cache_key` is set on a task that is not compile |
| `inputs-mismatch` | the request inputs break the per-type rule above |
| `outputs-not-declared` | the output kinds are not the declared outputs of one task type, in order |
| `duplicate-class` | the table lists a class twice |
| `unknown-class` | a table row is in neither source file and is not a runner, Temporal or placement row, or a failure names a type the table lacks |
| `class-not-reportable` | a failure names a `temporal` or `placement` class |
| `source-mismatch` | a workload row names another pool than `exit-classes.yaml`, or a row's source is not the file its class lives in |
| `deterministic-mismatch` | a row's `deterministic` differs from its source file, or failure details differ from their row |
| `reason-prefix` | a failure reason does not start with the class and `: ` |
| `deterministic-retried` | a `true` row has a budget other than `none` |
| `transient-never-retried` | a `false` or `from-error-file` row has the budget `none` |
| `bad-action` | `escalate-once` is on a row other than a guest workload row of compile or aggregate, `invalidate-cache-and-rebuild` or `previous-checkpoint` on a row that is not an artifact row, or `re-place` on a row that is not a Temporal row |
| `class-not-covered` | a class of `exit-classes.yaml` other than `ok` or a code of `failures.yaml`, read through package data, has no row |

A `failure-details` vector is the pair `{type, details}` that a worker raises, so the loader can check the details against the row of their type.
Vectors per schema, under `vectors/<name>/`: valid files for each task type of `task-input` and `task-result`, a resumed `task-input`, a deterministic `guest_failed`, a `STORE_UNAVAILABLE` and an `operator_interrupt` with a checkpoint for `failure-details`, a guest beat, a warm-up beat and a beat with a checkpoint for `heartbeat`, and the pinned table for `failure-classes`, plus at least one invalid file under every reason above that applies to the schema.

#### Temporal contract detail

Files: `VERSION`, `README.md`, `check.sh`, `naming.yaml`, `pools.yaml`, and the generated `naming`, `pools`, `control-signal`, `resume-signal` and `status-query` schemas.

`naming.yaml`:

| Key | v1 value | Rule |
| --- | --- | --- |
| `namespace` | `wdf` | D11 |
| `workflow` | type `JobWorkflow`, queue `wdf-workflow`, id `wdf_keys.workflow_id(job_id)` with reuse policy `REJECT_DUPLICATE`, versioning `PINNED`, deployment `wdf-workflow-worker` with the build id of `WDF_BUILD_ID` | D6, the only versioned queue |
| `control_queue` | `wdf-control-<build id>`, with the build id of `WDF_BUILD_ID` | one per build and unversioned (D12) |
| `pools` | `compile: wdf-compile`, `simulate: wdf-simulate`, `aggregate: wdf-aggregate` | keys equal the pools of `contracts/job/registry.json`, unversioned |
| `activities.pool` | `run_task`, single attempt, cancellation type `WAIT_CANCELLATION_COMPLETED` | D20 |
| `activities.control` | `load_plan`, `cache_lookup`, `cache_record`, `cache_invalidate`, `commit`, `index_checkpoint`, `build_index`, each with at most 5 attempts and backoff from 1 s to 60 s | D2, D12, D13 |
| `activities.local` | `place` and `emit`, each with no attempt limit and backoff from 1 s doubling to 600 s | D12, D15 |
| `signals` and `queries` | `cancel`, `terminate`, `pause`, `resume`; `status` | D19 |
| `search_attributes` | `WdfTenant`, `WdfPriorityClass`, `WdfJobClass` and `WdfJobId` (keyword) and `WdfSubmittedAt` (datetime) | the five that #8 proposes |
| `schedules.artifact_reaper` | id, queue, workflow, workflow id and activity as `wdf_artifactreaper.schedule` delivers them | decision 16 |

`pools.yaml`, marked `estimate: true`, with `schedule_to_start_ceiling_s` 43,200 (D5):

| Pool | `cpu_milli` | `memory_mib` | `schedule_to_start_s` | `heartbeat_s` | `start_to_close_margin_s` |
| --- | --- | --- | --- | --- | --- |
| `compile` | 1000 | 1024 | 1800 | 60 | 120 |
| `simulate` | 250 | 384 | 1800 | 60 | 120 |
| `aggregate` | 1000 | 1024 | 1800 | 60 | 120 |

The slots equal `deploy/presets/slots/ci.yaml`, and a test holds them equal.
`StaticPlacer` takes the queue of a pool from `pools` of `naming.yaml`, so the queue names live in one file.
The start-to-close timeout is computed per task as its run timeout plus the margin, because `run_timeout_s` is a field of each plan task and only compile's is fixed (`compile_run_timeout_s` 600).
The heartbeat timeout of 60 s is above S6's 30 s first run and well inside S3's cancel latency margin, and the margin of 120 s covers the warm-up of decision 14 and staging.
`StaticPlacer` returns `insufficient_memory` or `insufficient_cpu` when a request exceeds the slot of its pool, and `no_matching_pool` when the registry gives no pool.

`ControlSignal` for `cancel`, `terminate` and `pause` is `{control_seq, reason}`, with `control_seq` an integer from 1 to 2,147,483,647 (`ControlRequest.control_seq`) and `reason` a string of at most 1,024 characters or null (`wdf_events.models.Request.reason`).
`ResumeSignal` for `resume` is `{control_seq, manifest}`, with `manifest` the row's `pending_patch_ref` as a `wdf_events.models.ManifestReference`, or null without a patch.

`StatusQuery`, the result of query `status`, at most 524,288 bytes:

| Field | Rule |
| --- | --- |
| `job_status` | a status of `contracts/status/status.yaml` |
| `pause_reason` | `operator`, `retries-exhausted` or `resume-compile-failed` (decision 19), set exactly when `job_status` is `paused` |
| `resume_counter` | at least 0 |
| `control_seq` | the last applied `control_seq` of D19, which lets #24 confirm delivery |
| `tasks` | 1 to `max_tasks` entries in ascending task id, each with `id`, `state`, `task_attempt` (0 before the first run, which is how the query lists the tasks that never ran, decision 19), `guest_left`, `infra_left`, `reschedules`, `last_class` (a row of the table or null) and `checkpoint` (the head of D2's list or null) |

A task `state` is `pending`, `ready`, `running`, `succeeded`, `skipped`, `failed`, `blocked`, `paused` or `cancelled`.
`running` means an activity is scheduled on a pool queue, started or not, because the workflow cannot see a start (D14), and `blocked` means a dependency failed or paused.
`skipped` means a compile task that a cache hit satisfied (`task.skipped` with reason `compile-cache-hit`), and it counts as succeeded for its dependents, the job result and a resume.
The `skipped_tasks` of `job.partial` are another set: every task that is neither `succeeded` nor `skipped` when the job ends.

Temporal loader reasons, in check order: `schema-violation`, `too-large`, `duplicate-name` (a name used twice across queues, activities, signals and the query, except `run_task` on its pool queues), `pool-mismatch` (the pools of `naming.yaml` or `pools.yaml` differ from the registry), `unknown-status`, `unknown-class`, `pause-reason-mismatch` and `out-of-order`.
Each schema has the pinned file or two valid payloads, and at least one invalid file under every reason that applies to it.

### Rules agreed with epics that do not exist yet

These are stated so the owner epic confirms them in its own spec.

| Epic | Rule |
| --- | --- |
| #9 Placement | Ships a `Placer` that passes `placer_contract`, replaces `contracts/temporal/pools.yaml` with real values and timeout formulas by a contract-change PR, keeps the queue names of the naming contract, and answers `place(request, escalate=True)` with a larger memory class and `escalated=True` where one exists |
| #10 Workers | Serves `run_task` with the task input, task result, failure details and heartbeat details of `contracts/activity/` and passes `activity_contract`, raises the classes of the failure class table as the `ApplicationError` type, puts the latest checkpoint reference in both heartbeat and failure details, answers a cancel of a checkpoint-capable simulate task with `operator_interrupt` and its final checkpoint, heartbeats during the warm-up of decision 14, and keeps every pool queue unversioned |
| #8 Admission | Commits the move to `admitted` before it starts the workflow (D18), starts `JobWorkflow` on `wdf-workflow` with `workflow_id(job_id)`, `REJECT_DUPLICATE`, the five search attributes and the accepted event and params block as the two arguments, and sends `resume` with the row's `control_seq` and pending patch only while the row's `requested_action` is `resume` (D19) |
| #24 Dispatch and control | Sends `cancel`, `terminate` and `pause` with the row's `control_seq`, retries until delivered, reads `status` for detail, and writes `pending_patch_ref` as a `ManifestReference` |
| #18 Checkpoint | Builds segments and continue-as-new on this epic's workflow, retry loop, pause state and resume signal (D7), and owns the checkpoint format behind the references |
| #13 Observability | Reads the workflow's metrics, which follow the instrumentation contract and which #13 adds to `metrics.yaml`: `wdf_workflow_history_events`, `wdf_workflow_retries_total` by budget, `wdf_workflow_reschedules_total`, `wdf_workflow_paused_jobs`, `wdf_workflow_lost_transitions_total` and `wdf_workflow_replay_failures_total`, and the Temporal `cache_usage` series and `memory.events` `max` counter named in S10. #13 owns the alert rules on them, the history length alert included |
| #2, #16 | The control reserve of S8 counts one workflow worker at 256 MiB, and D21 runs two during a rollout, which the 14 MiB margin of the infra node does not hold once dispatch and admission run beside it. #16 measures the real reserve, and the cluster epic's table takes two workflow workers or a rollout procedure that pauses one build's growth |
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
| SDK and server | `temporalio` 1.35.0 in `uv.lock` (S3 ran 1.34.0 with server 1.32.1), the T1 dev server of the CLI image `temporalio/temporal` 1.9.1, which embeds server 1.32.0, and no chart or server image pinned (`charts: {}`). The proposed pins are the official chart `temporal` 1.8.0 from `https://go.temporal.io/helm-charts`, whose default server is 1.32.2, with `temporalio/server` and `temporalio/admin-tools` 1.32.2, which S10 confirms and M.1 pins by digest. Worker deployment versioning is generally available from server 1.31.0, server 1.32 needs SDK 1.30.0 or later, and Temporal supports SQL visibility on Postgres 12 or later, so `temporaldb` at 16.14 fits | `uv.lock:529-530`, `deploy/versions.yaml` [read], the chart's `Chart.yaml` and `values.yaml` at tag `temporal-1.8.0`, the server and CLI release notes [looked up] |
| Temporal budget | 1,024 MiB in the `wdf-infra` sum, and 256 MiB for the workflow worker in the `wdf-control` reserve. The CPU quota does not hold Temporal's 1 CPU (D22) | S8 (`docs/delivery/spikes/S8/state-footprint.md:159-166`), `scripts/cluster/namespaces.sh render ci` [read] |
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
| Reaper and its Schedule | Delivered by #12, deployment and the Schedule in `wdf` left | decision 16, M.2, D.1 |
| Temporal's Postgres | Delivered by #3 | `deploy/temporaldb/` |
| Temporal module, bootstrap, keys | Left | S10, M.1, M.2 |
| Activity and Temporal contracts, pool table v0 | Left | 0.1 (issue) |
| Workflow worker service and image | Left | D.1, D.2 |
| Job workflow and every behaviour of the epic | Left | P.1, P.2, A.1, A.2, W.1 to W.5 |
| Versioning, determinism gate, durability harness | Left | S9, V.1, V.2, V.3 |

Non-goals for this pass:

- everything the epic lists under "Does not own": admission, intake and the start call (#8, #7), the placement policy, real pool table and placement service (#9), the worker process, runner and classification (#10), the checkpoint contract and retention (#18), the plan format (#5), envelopes (#4), the job row and event table (#3), artifact storage (#12), the API handlers (#24), dashboards and alerts (#13) and cluster sizing (#2)
- segments and continue-as-new (D7), and the checkpoint format (decision 15)
- a pause longer than the retention safety net of `contracts/state/retention.yaml` (checkpoints 14 days), which is outside the proof of concept as the artifacts epic recorded in its decision 16, so a paused workflow sets no timer of its own
- the standard load of 100,000 jobs on the Helm install, which is #16's run

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with `uv`, Python, `helm`, `kubectl`, `kind`; recipes `just test`, `just itest`, `just ci`, `just up`, `just down`, `just nuke`; `wdf_itest` | `flake.lock`, `uv.lock` (`temporalio` 1.35.0) | none, exists [read] |
| Cluster | Preset `ci` (infra node 5,120 MiB, `wdf-infra` quota 4,772 MiB, `wdf-control` on the infra pool), module contract, topology, namespaces, cluster lock | `deploy/presets/ci.yaml`, module v1, topology v1 | none, exists |
| Envelope | Event models and codec, key formulas, status table, stage table and `run_stage_contract`, `wdf_temporal` converter, tracing, corpus and replay runner, `wdf_compat` window | events v3, keys v1, status v1, stages v1, temporal v1, compat v1 | none, exists |
| Instrumentation | Metric, label, log and trace rules: every workflow metric is `wdf_` lower case, a counter ends in `_total`, and no label holds a job or task id | instrumentation v1 | none, exists |
| Validator | Plan models, `read_plan`, `bind`, `stats`, `patch_check`, limits `ci` (history estimates, `compile_run_timeout_s` 600, `compile_retries` 1) | job v2, plan v1, patch v1 | none, exists |
| State | `transition`, `insert_events`, roles `wdf_workflow`, `wdf_artifacts` and `wdf_reaper`, `deploy/temporaldb/`, store roles `workflow`, `indexer` and `reaper` | state v2, store v3, eventstore v1, jobstate v1 | none, exists |
| Artifacts | `commit`, `cache.lookup`, `use`, `record`, `invalidate`, `build_index`, `put_index`, `verify_checkpoint`, `write_checkpoint`, typed errors, failure taxonomy, staging, index format, reaper Schedule | artifact v3, artifactclient v1, artifactreaper v1 | none, exists |
| Toolchain | Exit class table, `wdf_runtime.pins`, `wdf_compile.bundle.info` | workload v1, runtime v1, compile v1 | none, exists |
| Placement (#9) | `place` and the real pool table | not built (E9) | `StaticPlacer` over pool table v0 |
| Workers (#10) | Pool activity workers | not built (E11) | `FakePool` |
| Admission (#8) | The start call and the `resume` signal | not built (E12) | tests and the harness start workflows and send `resume` themselves |
| Dispatch (#24) | Control signals | not built (E13) | tests send signals directly |
| Checkpoint (#18) | Checkpoint format | not built (E17) | checkpoint references written by `write_checkpoint` with fake content |

No `revisit` child issue is open against an earlier epic that blocks this epic.
This spec adds one: the `ci` infra node CPU of D22, a child of #2 that merges before S10.
Decisions 19 and 20 keep every pinned contract as it is.
The names below were checked free on `main` [read: no `packages/workflow/`, no `contracts/activity/`, no `contracts/temporal/`, no `deploy/temporal/`, no `deploy/workflow/`, no `docs/delivery/spikes/S9/` or `S10/`].

### Produces (each pinned precisely)

| To | What | Where it lives (path) | Shape | Version |
| --- | --- | --- | --- | --- |
| #10, #9, #18, #13, #15 | Activity contract | `contracts/activity/` | `task-input`, `task-result`, `failure-details`, `heartbeat` schemas, `failure-classes.yaml`, vectors, loader and reasons as 0.4 | activity v1 |
| #8, #9, #10, #24, #12, #13, #15, #16 | Temporal contract | `contracts/temporal/` | `naming.yaml`, `pools.yaml` v0, `control-signal`, `resume-signal` and `status-query` schemas, vectors, loader | temporal v1 |
| #8, #24, #17, #18 | Job workflow and worker service | `packages/workflow/` (`wdf_workflow`), `deploy/workflow/` | `JobWorkflow.run(accepted, params)`, `python -m wdf_workflow.service`, module `workflow-worker` in `wdf-control` | workflow v1 |
| #13 | Workflow metrics | the service's metrics endpoint | the names of the #13 rules row, under the instrumentation contract | workflow v1 |
| the cluster, every epic | Temporal module | `deploy/temporal/` | modules `temporal` and `temporal-bootstrap` (module v1), the pinned chart or the fallback of decision 20, the two keys, namespace `wdf`, the search attributes and the reaper Schedule | module v1 |
| #9, #10 | Conformance tests and stubs | `wdf_workflow.placement`, `wdf_workflow.testing` | `Placer`, `StaticPlacer`, `placer_contract`, `FakePool`, `activity_contract`, `FakeControl`, `control_activity_contract` | workflow v1 |
| #15, #16, #17 | Harness | `wdf_workflow.harness` | `describe`, `smoke`, `restart`, `rollout` | workflow v1 |
| #4 compat rules, #18 | Recorded histories of `JobWorkflow` | `packages/workflow/corpus/v1/` and its `owners.yaml` (D24) | corpus files of `wdf_temporal.corpus` | none |
| #2, #16 | Spike results | `docs/delivery/spikes/S9/versioning.md`, `docs/delivery/spikes/S10/helm-install.md` | pass or fail, numbers, decision | none |

## 3. Lanes

Package names are proposals.
Lane 0 owns every file it creates until the owning lane takes over the glob, except the contract modules of `wdf_workflow` and `wdf_workflow.testing`, which stay lane 0's.

Departures from the method, stated so the review approves them knowingly:

- Spikes S9 and S10 run beside lane 0, because they write only under `docs/delivery/spikes/`.
  S10 drives the cluster under the lock and gates M.1, and S9 gates V.2.
- Lane 0 carries no `just/*.just` module and adds no recipe: tests run through `just test`, `just itest` and `just ci`, and the service and the harness are `python -m` commands, as the toolchain epic recorded in its decision 2.
- Lane W is one worker in sequence, because the workflow class is one file whose behaviours each issue extends.
- Lane Needs run across lanes where a test over a stub would prove only the stub: the workflow runs the pure rules (W.1 after P.1, W.2 after P.2), the service runs the workflow and the real control activities (D.1 after W.1 and A.2), the deployment needs the bootstrap (D.2 after M.2), M.1 needs S10 and the D22 revisit, and lane V drives the workflow, the service and the deployment.
- `docs/delivery/method.md` is not edited by this spec; its verifier table still lists `verify-execution`, a follow-up the earlier specs record the same way.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Contracts and skeleton | 1 | `contracts/activity/**`, `contracts/temporal/**`, `packages/workflow/{pyproject.toml,README.md}`, `packages/workflow/src/wdf_workflow/{__init__,models,contract,gen,placement}.py` (the `place` local activity is in `placement.py`), the package data under `packages/workflow/src/wdf_workflow/contract/**`, `packages/workflow/src/wdf_workflow/testing/**`, `packages/workflow/tests/{conftest,test_loader,test_drift,test_task_input,test_pools,test_placer_contract,test_activity_contract,test_control_contract,test_reaper_names,test_wheel}.py`, the two `wdf-workflow` lines of the root `pyproject.toml`, the `uv.lock` workspace entry, the `activity` and `temporal` rows of `docs/contracts.md`, `docs/architecture/15-contracts.drawio` and its svg, `docs/architecture/12-dag-and-workflow.drawio` and its svg | 0.1, 0.2 | nothing (barrier, merged alone) |
| S Spikes | 2 (one per spike) | `docs/delivery/spikes/S9/**`, `docs/delivery/spikes/S10/**`, the S9 and S10 rows of `docs/delivery/spikes/README.md` | S9, S10 | nothing, beside lane 0, and the D22 revisit for S10 |
| M Temporal module | 1 | `deploy/temporal/**` (module `temporal`, its `values.json` and render test, and module `temporal-bootstrap` in `deploy/temporal/bootstrap/`), the entries `charts.temporal`, `images.temporalio/server` and `images.temporalio/admin-tools` of `deploy/versions.yaml` | M.1, M.2 | lane 0, S10 and the D22 revisit for M.1, M.1 for M.2 |
| P Pure rules | 1 | `packages/workflow/src/wdf_workflow/{ready,result,failures,budgets}.py`, `packages/workflow/tests/test_{ready,result,failures,budgets}.py` | P.1, P.2 | lane 0 |
| A Control activities | 1 | `packages/workflow/src/wdf_workflow/activities/**` (the `emit` local activity included), `packages/workflow/tests/activities/**` | A.1, A.2 | lane 0, A.1 for A.2 |
| W Job workflow | 1 | `packages/workflow/src/wdf_workflow/{job,taskpath,retry,control,pause,aggregate}.py` (the status query and the workflow metrics in `job.py`), `packages/workflow/tests/workflow/**` | W.1, W.2, W.3, W.4, W.5 | lane 0, P.1 for W.1, P.2 for W.2 |
| D Service and deployment | 1 | `packages/workflow/src/wdf_workflow/service/**`, `packages/workflow/tests/service/**`, `deploy/workflow/**` (module `workflow-worker`, its image, the service logins and the store Secret of decision 24), the entry `images.python` of `deploy/versions.yaml`, the `deploy/temporal` and `deploy/workflow` entries of `TREES` in `packages/migrate/tests/test_credentials.py` | D.1, D.2 | W.1 and A.2 for D.1, D.1 and M.2 for D.2 |
| V Versioning, determinism, harness | 2 (V.2 and V.3 at once, V.1 by either) | `packages/workflow/src/wdf_workflow/versioning.py`, `packages/workflow/src/wdf_workflow/harness/**`, `packages/workflow/tests/{replay,versioning,harness}/**`, `packages/workflow/corpus/**` (the histories, their `owners.yaml` and the `README.md` with the rules for changing the workflow) | V.1, V.2, V.3 | W.5 for V.1, S9, W.1, D.1 and D.2 for V.2, W.2, A.2 and D.2 for V.3 |

Shared-file rules:

- `uv.lock` is the one hotspot, and 0.1 (issue) commits it with the workspace entry of `wdf-workflow`.
  Lane 0 declares in `packages/workflow/pyproject.toml` every dependency the lanes need (the workspace packages the Consumes table names and the locked `temporalio`, `psycopg`, `pydantic` and `pyyaml`), so no later lane edits it.
  A lock conflict is never resolved by hand, and `uv lock` is rerun after rebase.
- The root `pyproject.toml` changes only in 0.1 (issue), by one `wdf-workflow` line in `[tool.uv.sources]` and one in the dev dependency group.
- `deploy/versions.yaml` changes only in the entries a lane owns.
  M.1 replaces `charts: {}` with the `temporal` entry and adds its two image entries after `temporalio/temporal`, and D.2 adds `python` after `postgres`, so the hunks never touch.
- `packages/workflow/src/wdf_workflow/__init__.py` and `packages/workflow/tests/conftest.py` are lane 0's, and a lane imports by module path.
  Lane 0's conftest holds the fixtures that several lanes share (the dev server through `wdf_itest`), and a lane keeps its own fixtures in a `conftest.py` inside its own test directory.
- A test that needs a container carries `integration`, a test that needs the store and the database together also carries `pair`, and a test that starts or restarts containers of its own carries `isolated`.
  No pytest session holds more than two containers, so the tests of lanes W, D and V and of 0.2 (issue) use `FakeControl` and never the real control activities.
- `contracts/activity/README.md` and `contracts/temporal/README.md` are lane 0's, and after lane 0 merges a pinned file changes only through a contract-change PR.
- `docs/architecture/12-dag-and-workflow.drawio` and its svg are refined by 0.2 (issue) before any other lane starts (native completion, single attempt with the loop in the workflow, pause keyed on the checkpoint flag, the control activities), and the exit-test worker re-checks them at closure.
- `docs/delivery/spikes/README.md` gets one appended row per spike, and a conflict between the S9 and S10 rows is resolved by keeping both.
- `packages/migrate/tests/test_credentials.py` changes only in D.2, by adding `deploy/temporal` and `deploy/workflow` to `TREES`, so the credential scan covers both new module directories.
- No file under `deploy/temporal/` or `deploy/workflow/` other than `module.yaml` and Kubernetes manifests is YAML (D23).
- `justfile`, `just/*.just`, `.github/**`, `nix/**`, `scripts/**`, `docs/dev-setup.md`, `deploy/host/ports.yaml`, `packages/temporal/**`, every other package except the one `TREES` edit, and every other `deploy/` directory are not touched.
  Host access to Temporal, the database and the store goes through `kubectl port-forward` bound to 127.0.0.1.
- Generated schemas are committed, regenerated by `python -m wdf_workflow.gen`, and never hand edited.
- No tracked file holds a credential: passwords and store credentials come from the state directory, as the `temporaldb` module does.

## 4. Issues

One block per issue, in the [lane issue template](../../../.github/ISSUE_TEMPLATE/lane-issue.md), replaced by `#NNN` links after filing.
Done means, for every issue: `just ci` is green on the branch, the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files changed.
An issue that names a T2 command runs it under the cluster lock and pastes its output.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 Temporal module via Helm | S10, M.1, M.2 |
| 2 Workflow worker service | D.1, D.2 |
| 3 Job workflow v1 | P.1, W.1 |
| 4 Task path and `place` integration, compile cache check | 0.2 (issue) (`StaticPlacer` and `place`), W.1, A.2 |
| 5 Retry loop and failure budgets | 0.1 (issue) (the table), P.2, W.2 |
| 6 Schedule-to-start re-schedule | P.2, W.2 |
| 7 Aggregate semantics | P.1, A.2, W.3 |
| 8 Cancellation and control signals | W.4 |
| 9 Pause and resume | W.5 |
| 10 Worker versioning and upgrade policy | S9, V.2 |
| 11 Determinism gate | V.1 |
| 12 History budget | W.1 (the metric), W.2 (the shape and the enforcement of D16), the alert rule to #13, continue-as-new readiness moved to #18 (D7) |
| 13 State-change events | A.1 |
| 14 Durability test harness | V.3 |

### 0.1 Activity and Temporal contracts

Lane 0, tier T0. Needs: nothing. Blocks: every other issue but the spikes.

- Scope: the two contracts of 0.4 with their models, loader, generator, vectors and READMEs, the two `docs/contracts.md` rows, and the contracts diagram.
- Owns: `contracts/activity/**`, `contracts/temporal/**`, the `activity` and `temporal` rows of `docs/contracts.md`, `docs/architecture/15-contracts.drawio` and its svg, `packages/workflow/{pyproject.toml,README.md}`, `wdf_workflow/{__init__,models,contract,gen}.py`, the package data under `wdf_workflow/contract/`, `tests/{conftest,test_loader,test_drift,test_task_input,test_pools}.py`, the root `pyproject.toml` lines and the `uv.lock` entry.
- Consumes / produces: events v3, workload v1, artifact v3, job v2 and status v1, read through package data.
  Produces activity v1 and temporal v1.
- Acceptance criteria:
  - [ ] `contracts/activity/check.sh` and `contracts/temporal/check.sh` exit 0 for every `vectors/*/valid/*` file and the pinned `failure-classes.yaml`, `naming.yaml` and `pools.yaml`, and 1 with the `reason:` of its directory for every `invalid` one, shown by `pytest packages/workflow -v -k loader`.
  - [ ] `test_failure_table_covers_exit_classes` and `test_failure_table_covers_artifact_codes` pass on the pinned table and fail on the vectors under `invalid/class-not-covered/`.
  - [ ] `test_task_input_round_trip` passes: a `TaskInput` through `wdf_temporal.converter` keeps `request` byte-equal to `wdf_events.to_bytes`.
  - [ ] `test_pools_match_ci_slots` passes, and fails after a slot of `pools.yaml` is changed.
  - [ ] `python -m wdf_workflow.gen --check` exits 0, and exits 1 after a model field is renamed.
  - [ ] `scripts/check-contracts-map.sh` and `scripts/export-diagrams.sh --check` exit 0.
- Verifier: `packages/workflow/tests/test_loader.py`, `test_drift.py`, `test_task_input.py`, `test_pools.py`.

### 0.2 Package skeleton, stubs and fakes

Lane 0, tier T0, and T1 for the fake pool worker. Needs: 0.1 (issue). Blocks: every code issue.

- Scope: the wheel test, `Placer`, `StaticPlacer`, the `place` local activity and `placer_contract`, `FakePool` and `activity_contract`, `FakeControl` and `control_activity_contract`, the reaper name test, and the refined DAG diagram.
- Owns: the rest of lane 0's globs.
- Consumes / produces: pool table v0, the naming contract.
  Produces the stubs of section 5.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "placer_contract or activity_contract or control_contract"` passes against `StaticPlacer`, `FakePool` and `FakeControl`, and fails against a placer that returns a queue outside the naming contract, a fake that returns bytes instead of references, a fake that answers a cancel without `operator_interrupt` and a checkpoint, and a fake control activity that raises a plain exception.
  - [ ] `pytest packages/workflow -v -k reaper_names` passes, and fails when the naming contract's reaper Schedule id, workflow, activity or queue differs from `wdf_artifactreaper.schedule`.
  - [ ] `pytest packages/workflow -v -k wheel` builds the wheel and loads both contracts from package data outside the repository.
  - [ ] `scripts/export-diagrams.sh --check` exits 0 after the diagram refinement.
- Verifier: `test_placer_contract.py`, `test_activity_contract.py`, `test_control_contract.py`, `test_reaper_names.py`, `test_wheel.py`.

### S9 Versioning on SDK 1.35.0 and the pinned-bug override

Lane S, spike, timebox 1.5 days, tier T1, throwaway code in `docs/delivery/spikes/S9/code/`. Needs: nothing. Blocks: V.2.

- Question: does pinned versioning hold on `temporalio` 1.35.0 against the pinned dev server, do the raw deployment calls set the current version and move an open pinned workflow to a fixed build, and does a visibility count detect that a build is drained?
- Pass line, all five on the dev server of `temporalio/temporal` 1.9.1 (server 1.32.0, as the server reports) with `temporalio` 1.35.0:
  - (1) a workflow declared `@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)` on a worker with `WorkerDeploymentConfig(version=WorkerDeploymentVersion("wdf-workflow-worker", A), use_worker_versioning=True)`, started on A and waiting on a signal, finishes on A while B is current, as `workflow.info().get_current_deployment_version()` reports;
  - (2) the raw `client.workflow_service.set_worker_deployment_current_version` call, with the `conflict_token` of `describe_worker_deployment` and the client identity, makes A current on a fresh deployment and then B;
  - (3) the raw `update_workflow_execution_options` call with a `PinnedVersioningOverride` to B moves an open pinned workflow from A to B, and it completes on B's code, with B's change guarded by `workflow.patched`;
  - (4) an unversioned activity worker on a pool queue serves workflows of both builds;
  - (5) `count_workflows` with `TemporalWorkerDeploymentVersion = 'wdf-workflow-worker:A' AND TemporalWorkflowVersioningBehavior = 'Pinned' AND ExecutionStatus = 'Running'` returns 0 once the last pinned workflow on A closes, and the time until `describe_worker_deployment_version` reports `VERSION_DRAINAGE_STATUS_DRAINED` is recorded.
  - Any of the five failing is a fail.
- The result also records the update mask path that (3) needs, whether an override schedules a workflow task on its own, whether the search attributes follow a moved workflow before its next task completes, and what an override to a build with an unguarded change does, because the SDK source leaves these four open.
- On fail: the override procedure is "pause, terminate and resubmit the job, keeping its artifacts", recorded in the result and applied by V.2.
- Result: `docs/delivery/spikes/S9/versioning.md` with the server version the dev server reports.

### S10 The official Temporal chart on the `ci` preset

Lane S, spike, timebox 2 days, tier T2 under the cluster lock, throwaway values in `docs/delivery/spikes/S10/code/`. Needs: the D22 revisit. Blocks: M.1.

- Question: does the official chart `temporal` 1.8.0 (server 1.32.2), with Postgres persistence and SQL visibility on `temporaldb`, one replica per service, 512 shards and the two dynamic-config keys, fit Temporal's 1,024 MiB and 1 CPU on the `ci` infra node, and do the keys reach the history service?
- Values to start from: `server.config.persistence` with `numHistoryShards: 512` and the stores `default` and `visibility`, each `sql` with `pluginName: postgres12`, the database `temporal` or `temporal_visibility` on `temporaldb.wdf-infra.svc:5432`, the password from the `temporaldb` Secret, `createDatabase: false` and `manageSchema: true`; the two keys under `server.dynamicConfig`; `web.enabled: false`; `server.config.namespaces.create: false`; no top-level `cassandra`, `elasticsearch`, `mysql`, `postgresql`, `prometheus` or `grafana` key, because chart 1.8.0 has no subcharts and fails the render on them; and the manifests from `helm template --skip-tests` with `schema.useHelmHooks: false`.
- Starting split, an estimate from the S3b heap profile: history 576 MiB and 450m, frontend 192 MiB and 200m, matching 128 MiB and 200m, worker 128 MiB and 150m.
- Pass line, all of: (1) frontend, history, matching and worker run at one replica each with no Elasticsearch and no web UI, and the sum of their memory limits is at most 1,024 MiB and of their CPU limits at most 1; (2) 3,000 two-task stand-in jobs (the S3 method, with host workers through a port-forward bound to 127.0.0.1) all finish; (3) the history service's mutable state cache series stays at or below 1,000 while they finish; (4) the `max` counter of `memory.events` of no Temporal pod rises.
  Any of the four failing is a fail.
- The result records the exact name and labels of the cache series as the server exports it (expected `cache_usage{cache_type="mutablestate"}` on port 9090), because M.1 and the exit test read it by name.
- On fail: decision 20 applies, and M.1 deploys the one-pod shape: `temporalio/server:1.32.2`, whose default `temporal-server start` runs the four services in one process, configured by the embedded template's environment (`DB=postgres12`, `NUM_HISTORY_SHARDS=512`, `DYNAMIC_CONFIG_FILE_PATH` and `PROMETHEUS_ENDPOINT` set explicitly because their defaults are Cassandra, 4 shards and no metrics), with the schema set up by a job on `temporalio/admin-tools:1.32.2`.
- Result: `docs/delivery/spikes/S10/helm-install.md` with the chart and server versions and digests, the per-service limits and peaks, and the decision.

### M.1 Temporal module

Lane M, tier T2. Needs: lane 0, S10, the D22 revisit. Blocks: M.2.

- Scope: module `temporal` in `deploy/temporal/` per the module contract, the pinned chart (or the fallback of decision 20), persistence and visibility on `temporaldb`, 512 shards, one replica per service, the two keys of decision 18, the limits of S10, readiness, `profiles: [dev, full, ci]`, the values in `values.json` (D23), and a checksum of the dynamic config on the pod template, so a changed key rolls the Temporal pods, because both keys need a restart.
- Owns: lane M's globs.
- Consumes / produces: module v1, `deploy/temporaldb/`, S10.
  Produces module `temporal` and three `deploy/versions.yaml` entries.
- Acceptance criteria:
  - [ ] `just versions --check` exits 0 with the chart and both images pinned by digest, or, under the fallback, the two images and no chart.
  - [ ] `pytest deploy/temporal -v` passes `test_dynamic_config_keys`, `test_limits_match_s10` and `test_config_change_rolls_pods` (the checksum annotation changes when a key changes) against the module's render, the chart's or the fallback's.
  - [ ] `just up` then `scripts/tilt/run.sh ci` exits 0 with `temporal` ready beside `temporaldb`, and `kubectl --context kind-wdf -n wdf-infra get pods` lists one pod per Temporal service, or one under the fallback.
- Verifier: the commands above, output pasted.

### M.2 Bootstrap

Lane M, tier T2. Needs: M.1. Blocks: D.2.

- Scope: module `temporal-bootstrap`, an idempotent job that creates namespace `wdf` with the retention of D11, registers the five search attributes, and creates the reaper's Schedule in `wdf` with `wdf_artifactreaper.schedule.ensure_schedule` (decision 16).
- Owns: lane M's globs.
- Consumes / produces: the naming contract, `wdf_artifactreaper`.
  Produces the namespace, the search attributes and the Schedule.
- Acceptance criteria:
  - [ ] The bootstrap job completes twice in a row with exit 0, and the second run changes nothing, shown by its log.
  - [ ] Its log, or the equivalent `temporal operator` calls from the pinned CLI image, prints retention `24h`, the five search attributes with their types, and Schedule `wdf-artifact-reaper` in `wdf`.
  - [ ] `scripts/tilt/run.sh ci` exits 0 with `temporal-bootstrap` completed.
- Verifier: the commands above, output pasted.
- Note: the keys-active check of the epic's detail issue 1 is S10's pass line on the chart and exit row 12 on the module.

### P.1 Ready set and job result model

Lane P, tier T0. Needs: lane 0. Blocks: W.1.

- Scope: pure functions with no Temporal import: the ready set in ascending task id under `max_parallel`, fan-out and fan-in, blocked dependents, aggregate readiness with and without `allow_partial`, both drain modes of D8, pause or fail of D9 with its resume-compile exception and of decision 23, the terminal status (`succeeded`, `partial`, `failed`, `cancelled`, `terminated`) or `paused`, `skipped_tasks` of a partial run, and the per-task states of the status query.
- Owns: lane P's globs for `ready` and `result`.
- Consumes / produces: plan v1, status v1, the status query schema.
  Produces `wdf_workflow.ready` and `wdf_workflow.result`.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "ready or result"` passes, including Hypothesis properties: no task becomes ready before its dependencies succeed, at most `max_parallel` tasks run, the order is a function of the plan alone, and every task ends with a state.
  - [ ] Named cases pass: `test_aggregate_waits_for_all`, `test_allow_partial_lists_skipped`, `test_paused_and_failed_chain_pauses`, `test_resume_reruns_unsucceeded`, `test_manual_pause_cancels_only_checkpoint_tasks`, `test_resume_compile_failure_pauses` and `test_resume_keeps_skipped_compile`.
- Verifier: `test_ready.py`, `test_result.py`.

### P.2 Failure decisions and budgets

Lane P, tier T0. Needs: lane 0, and P.1 for its worker. Blocks: W.2.

- Scope: `decide(failure, task, budgets) -> Decision` over the failure class table: guest and infrastructure budgets, `none` rows ending the task at once, `free` rows consuming nothing, the OOM rule of decision 22 with D3's two-in-a-row rule, the backoffs of D3 and D5, `invalidate-cache-and-rebuild`, `previous-checkpoint` over D2's list and `re-place`, and the history estimate per decision.
- Owns: lane P's globs for `failures` and `budgets`.
- Consumes / produces: the activity v1 table, plan `retries`.
  Produces `wdf_workflow.failures` and `wdf_workflow.budgets`.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "failures or budgets"` passes, with one case per row of the table (`test_every_class_row_has_a_case`).
  - [ ] `test_deterministic_class_not_retried`, `test_infra_cap_exhausted`, `test_operator_interrupt_free`, `test_repeated_oom_killed_stops` and `test_schedule_to_start_backoff_ceiling` pass.
- Verifier: `test_failures.py`, `test_budgets.py`.

### A.1 Plan load and the emit point

Lane A, tier T1 (Postgres and SeaweedFS through `wdf_itest`, marked `pair`, as plain functions without Temporal). Needs: lane 0. Blocks: A.2.

- Scope: `load_plan` (D1), the batched `emit` local activity that writes events with `insert_events` after the id check of D17 and projects status with `transition` under a login in `wdf_workflow` (decision 24), deterministic ids from `cloudevent_id` and `time` from the fact, the `Lost` cases of D18, and the `ApplicationError` types of D12.
- Owns: lane A's globs.
- Consumes / produces: state v2, eventstore v1, jobstate v1, artifactclient v1, plan v1.
  Produces the two activities.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "load_plan or emit"` passes, including `test_emit_replay_inserts_nothing`, `test_emit_retry_with_shifted_time_inserts_nothing` (the same ids with every time moved by 1 s insert 0 rows) and `test_manifest_hash_mismatch_refused`.
  - [ ] `test_emit_lost_cases` shows the four answers of D18, and `test_activities_run_under_their_role` connects with a test login in `wdf_workflow` and never as the superuser.
  - [ ] `control_activity_contract` passes against `load_plan` and `emit`.
- Verifier: `tests/activities/test_load_plan.py`, `test_emit.py`.

### A.2 Commit, compile cache and aggregate index activities

Lane A, tier T1, marked `pair`. Needs: A.1. Blocks: D.1, V.3.

- Scope: `commit` of the accepted attempt (decision 11) under a login in `wdf_artifacts` (decision 24), `index_checkpoint` of D2 under the same login, `cache_lookup` (D13), `cache_record` with the build-info key check, the guarded `cache_invalidate` of decision 10, which looks the entry up with `SELECT ... FOR UPDATE` in the transaction of its `invalidate_cache_entry`, so no entry recorded between the lookup and the invalidation is retired, and `build_index` from recorded results in task id order, which puts the index with the `indexer` store role and commits its reference with `commit` and no accepted attempt under the `wdf_artifacts` login and a `workflow` store client.
- Owns: lane A's globs.
- Consumes / produces: artifactclient v1, keys v1, runtime v1 pins, compile v1 `bundle.info`.
  Produces the remaining control activities.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "commit or cache or build_index or index_checkpoint"` passes, including `test_cache_error_not_miss`, `test_bundle_without_lockfile_has_no_key`, `test_build_info_key_mismatch_refused`, `test_stale_invalidate_keeps_fresh_entry`, `test_cache_invalidate_races_fresh_record`, `test_commit_refuses_zombie_attempt`, `test_commit_upload_failed_reaches_workflow`, `test_index_checkpoint_refuses_other_attempt`, `test_index_in_task_id_order` and `test_build_index_commits_reference`.
  - [ ] `control_activity_contract` passes against every activity of this issue.
  - [ ] `test_cache_key_matches_build_info`, marked `slow` and not `integration`, computes the key of the workload bundle of `wdf_workloads.bundle` and finds it equal to the `cache_key` printed by `python -m wdf_compile build` for that bundle, and runs in the exit test.
- Verifier: `tests/activities/test_commit.py`, `test_checkpoint.py`, `test_cache.py`, `test_index.py`.

### W.1 Job workflow v1 and the task path

Lane W, tier T1 (dev server through `wdf_itest`, with `FakeControl` and `FakePool`). Needs: lane 0, P.1. Blocks: W.2, D.1, V.2.

- Scope: `JobWorkflow.run(accepted, params)` with the payload converter and tracing interceptor of `wdf_temporal`, plan load, the ready loop, `place` as a local activity through `StaticPlacer`, a single-attempt activity on the returned queue with the returned timeouts and the cancellation type of D20, the task input of 0.4, the compile cache check before placement (skip on a hit with `task.skipped`), commit, emit with the defect rule of D12, the status query, the workflow metrics of the #13 rules row, and `run_stage_contract("workflow", ...)`.
- Owns: lane W's globs.
- Consumes / produces: P.1, the naming and activity contracts.
  Produces the workflow.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "fan_out_fan_in or max_parallel or cache_hit_skips or status_query or stage_contract_workflow or workflow_metrics_recorded"` passes.
  - [ ] `test_place_answer_recorded` shows the placement as a marker in the history and no `place` call on replay.
- Verifier: `tests/workflow/test_job.py`, `test_taskpath.py`.

### W.2 Retry loop, schedule-to-start, lost completion and the history budget

Lane W, tier T1. Needs: W.1, P.2. Blocks: W.3, V.3.

- Scope: the decisions of P.2 in the workflow, backoff timers, D2's checkpoint list with `index_checkpoint` and `task.checkpoint.committed` for each reference received, the re-place after a schedule-to-start timeout, the scheduler-down behaviour of D15, and the history shape and enforcement of D16.
- Owns: lane W's globs.
- Consumes / produces: P.2.
  Produces the loop.
- Acceptance criteria:
  - [ ] `test_lost_completion_job_finishes`: a `FakePool` worker does the work and never completes, the heartbeat timeout is retried from the infrastructure budget, and the job finishes with only the second attempt committed.
  - [ ] `test_zombie_fenced`: a timed-out attempt's next heartbeat is refused, and its outputs are never committed.
  - [ ] `test_schedule_to_start_replaces`, `test_scheduler_down_beyond_task_timeout`, `test_deterministic_class_not_retried_in_workflow`, `test_history_budget_ends_job` and `test_checkpoint_indexed_and_committed` pass.
  - [ ] `test_history_shape` prints events per job, per task and per retry and the events one hour of placement outage adds, and the PR records them against 10, 8 and 6.
- Verifier: `tests/workflow/test_retry.py`.

### W.3 Aggregate semantics

Lane W, tier T1. Needs: W.2. Blocks: W.4.

- Scope: the `build_index` call before an aggregate task, the aggregate-only job taking its index reference from its parameters, `allow_partial`, and the `partial` terminal status with `job.partial`.
- Owns: lane W's globs.
- Consumes / produces: P.1, the index activity name.
  Produces aggregate handling.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "aggregate_waits_for_all_inputs or allow_partial_partial_status or aggregate_only_job or aggregate_gets_index_reference_only"` passes.
- Verifier: `tests/workflow/test_aggregate.py`.

### W.4 Cancellation and control signals

Lane W, tier T1. Needs: W.3. Blocks: W.5.

- Scope: `cancel`, `terminate` and `pause` ordered as D19 says, catching both the asyncio and the Temporal cancelled exception, cancelling in-flight tasks and re-raising, the manual pause of decision 7 with `operator_interrupt` under D20 and its rule of D8 for a task that never started, the `Lost` answers of D18, and projection of `cancelled`, `terminated` and `paused`.
- Owns: lane W's globs.
- Consumes / produces: the signal schemas.
  Produces control handling.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "cancel_in_flight or terminate_hard_stop or signal_dedup or resume_and_cancel_same_seq_cancel_wins or cancel_while_resume_pending or manual_pause_interrupts_checkpoint_task or manual_pause_keeps_final_checkpoint or manual_pause_drains_others or manual_pause_queued_task_returns_ready or manual_pause_no_replace or lost_terminal_completes or unexpected_lost_resends"` passes.
- Verifier: `tests/workflow/test_control.py`.

### W.5 Pause and resume

Lane W, tier T1, and T2 in the exit test. Needs: W.4, and A.1 for the T2 rows. Blocks: V.1.

- Scope: pause on exhausted budgets per D9 and decision 23, waiting on `resume` with no polling, a patched manifest reloaded through `load_plan`, the projection of `resume-pending` to `running` with `job.resumed` before anything else runs, the compile of a patched source re-run (cache keyed), the job moved from `running` back to `paused` with reason `resume-compile-failed` when that compile fails, because the status table has no move from `resume-pending` to `paused`, resume from the head of D2's list with the next resume counter, and the next reference after `CHECKPOINT_CORRUPT`.
- Owns: lane W's globs.
- Consumes / produces: the resume signal schema, patch v1.
  Produces pause and resume.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "drain_then_pause or resume_from_checkpoint or resume_compile_failure_stays_paused or corrupt_checkpoint_falls_back or paused_costs_no_history"` passes, where `resume_compile_failure_stays_paused` checks the moves that `FakeControl` recorded and `paused_costs_no_history` shows the history length unchanged across a 60 s pause.
- Verifier: `tests/workflow/test_pause.py`.

### D.1 Workflow worker service

Lane D, tier T1. Needs: W.1, A.2. Blocks: D.2, V.2.

- Scope: `python -m wdf_workflow.service` as 0.4, configuration from the environment, SDK Prometheus metrics and the workflow metrics, graceful shutdown, and the reaper's Schedule worker in the same process on a client of namespace `wdf` (decision 16).
- Owns: lane D's globs.
- Consumes / produces: the naming contract, `wdf_artifactreaper.schedule`.
  Produces the service.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "service_starts_three_workers or control_queue_per_build or sigterm_drains or metrics_endpoint or missing_env_refused"` passes against the dev server, with `FakeControl` standing in for the control activities' database and store.
- Verifier: `tests/service/test_service.py`.

### D.2 Image and deployment

Lane D, tier T2. Needs: D.1, M.2. Blocks: V.2, V.3.

- Scope: the service image on a pinned Python base with `WDF_BUILD_ID` taken from the environment of the build, falling back to the short commit, because the exit test runs from an export with no commit; module `workflow-worker` in `wdf-control` as one Deployment per build id (D21) with requests equal to limits at 256 MiB (S8's reserve, an estimate); an idempotent job that creates the three service logins of decision 24 in their group roles with passwords generated into the state directory; a Secret holding only the `workflow`, `indexer` and `reaper` store identities; and readiness.
- Owns: lane D's globs.
- Consumes / produces: module v1, the namespaces, state v2 roles, store v3 roles.
  Produces module `workflow-worker`.
- Acceptance criteria:
  - [ ] `scripts/tilt/run.sh ci` after `just up` exits 0 with `workflow-worker` ready, and its pod log shows the workflow, control and reaper workers polling with the build id.
  - [ ] Running the login job twice exits 0 both times, and `psql` as the superuser lists the three logins, each a member of only its group role.
  - [ ] `pytest packages/migrate -v -k credentials` passes with the two new trees.
- Verifier: the commands above, output pasted.

### V.1 Determinism gate

Lane V, tier T1. Needs: W.5. Blocks: the exit test.

- Scope: recorded histories of `JobWorkflow` in `packages/workflow/corpus/v1/` (D24) covering a plain job, a retry, a cache hit, a partial aggregate, a cancel and a pause with resume, replayed with `wdf_temporal.corpus.replay_corpus([JobWorkflow], root)` in CI, a nondeterminism negative test, and the rules for changing the workflow in `packages/workflow/corpus/README.md`.
- Owns: lane V's globs.
- Consumes / produces: the corpus tools of #4.
  Produces histories and the gate.
- Acceptance criteria:
  - [ ] `pytest packages/temporal packages/workflow -v -k replay` passes, and `test_replay_detects_reordered_activity` fails the replay of a history against a changed workflow.
- Verifier: `tests/replay/test_replay.py`.

### V.2 Worker versioning and rollout

Lane V, tier T1, and T2 in the exit test. Needs: S9, W.1, D.1, and D.2 for the T2 rows. Blocks: the exit test.

- Scope: `wdf_workflow.versioning`, which sets the current build with the raw `set_worker_deployment_current_version` call and the conflict token of `describe_worker_deployment`, after the new build's pollers are seen and without `allow_no_pollers`; counts the open pinned workflows of a build with the query of S9's item (5), a count of 0 meaning drained, and reports the server's drainage status beside the count without waiting on it, because the server refreshes it every 3 minutes after a 3-minute grace period by default; moves an open pinned workflow, a paused one included, to a fixed build with the override of D6 or applies the fallback S9 records; and deletes an old build's Deployment only when drained (D21).
- Owns: lane V's globs.
- Consumes / produces: S9.
  Produces `wdf_workflow.versioning`.
- Acceptance criteria:
  - [ ] `pytest packages/workflow -v -k "pinned_finishes_on_old_build or first_deploy_sets_current or drain_detects_no_pinned or override_procedure or only_workflow_queue_versioned"` passes against the T1 dev server, where the last shows `deployment_config` on the `wdf-workflow` worker and on no other worker the service builds.
- Verifier: `tests/versioning/test_versioning.py`.

### V.3 Durability harness

Lane V, tier T1, and T2 in the exit test. Needs: W.2, A.2, D.2. Blocks: the exit test.

- Scope: `python -m wdf_workflow.harness` with `describe`, `smoke`, `restart` (the Temporal server or the workflow worker with tasks in flight, with `--target` naming the pods, and the Temporal pod under decision 20) and `rollout`, with the flags, scenarios and output lines of 0.4, seeding rows and manifests and running `FakePool` workers on the host.
- Owns: lane V's globs.
- Consumes / produces: every lane.
  Produces the harness for #15 and #16.
- Acceptance criteria:
  - [ ] At T1, `pytest packages/workflow -v -k "harness_restart_worker or harness_t1_refuses_row_scenarios"` passes, the first with all jobs finished, using `FakeControl` and seeding nothing, because the dev server, Postgres and the store together are three containers, and the second showing exit 2 for `pause-resume` and `zombie`.
  - [ ] At T2, `python -m wdf_workflow.harness --kube-context kind-wdf smoke --jobs 200` prints `finished: 200` and `failed: 0`.
- Verifier: `tests/harness/test_harness.py`, the T2 command output pasted.

## 5. Order, stubs and spikes

- First, in parallel: 0.1 (issue), S9, and the D22 revisit of #2, then S10 after the revisit.
  Then 0.2 (issue).
  Lane 0 (0.1 and 0.2) merges alone, with an estimate of four days.
- After lane 0: P.1, A.1, and M.1 once S10 has passed.
- After P.1: W.1, and P.2 by lane P's one worker.
  After A.1: A.2.
  After M.1: M.2.
  After W.1 and P.2: W.2.
  After W.1 and A.2: D.1.
  After D.1 and M.2: D.2.
  Then W.3, W.4 and W.5 in order.
  After S9, W.1, D.1 and D.2: V.2.
  After W.2, A.2 and D.2: V.3.
  After W.5: V.1.
- DAG: (0.1 ‖ S9 ‖ D22 revisit then S10), then 0.2, then (P.1 ‖ A.1 ‖ M.1), then (W.1 ‖ P.2 ‖ A.2 ‖ M.2), then (W.2 ‖ D.1), then (W.3 ‖ D.2), then (W.4 ‖ V.2 ‖ V.3), then W.5, then V.1, then the exit test.
- Cluster work: S10, M.1, M.2, D.2, the T2 halves of V.2 and V.3, and the exit test, each under the cluster lock, one at a time.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| `StaticPlacer` over pool table v0 | the placement service of #9 | #9 runs `placer_contract` against its client and replaces `pools.yaml` by a contract-change PR |
| `FakePool` | the pool workers of #10 | #10 runs `activity_contract` against its worker on each pool queue |
| `FakeControl` | the control activities of lane A | A.1 and A.2 run `control_activity_contract` against the real activities, and the T2 rows of the exit test run them inside the deployed service |
| Workflow starts and `resume` signals sent by tests and the harness | admission (#8) | #8 starts with the options of the naming contract and the resume rule of D19, and the harness start path is its reference |
| Control signals sent by tests | the control API (#24) | #24 sends the signal schemas of the temporal contract |
| Checkpoint references with fake content | the checkpoint format of #18 | #18's kill-and-resume test runs on this workflow |

Spikes: S9 and S10, as their blocks in section 4 state, run beside lane 0 and gate V.2 and M.1.

## 6. Verifier delta

None, per decision 1: no skill, no feature file, no verify recipe.
Each issue names its tests in section 4, and the exit test in section 7 proves the epic from a clean export.

## 7. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main` taken outside any git repository, on the 20 GiB WSL2 host with Docker running for the whole run.
Containers other than the ones the run starts are never touched.
Each command runs from the export root, its real output is pasted in the PR that records the run, and it is summarised in Closure.
Every command takes `nix develop -c`, and every Python command takes `uv run --frozen`.
Rows 10 to 17 take the cluster lock for their whole sequence (`flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 WDF_BUILD_ID=exit-a bash -c '<commands>'`), and row 18 runs outside it.
`H` stands for `python -m wdf_workflow.harness --kube-context kind-wdf`, which opens and re-opens its own port-forwards, bound to 127.0.0.1.
Row 12 opens one more, `kubectl --context kind-wdf port-forward --address 127.0.0.1` to the history service's metrics port (`<mport>`), and closes it at the end of the row.
"The Temporal pods" means one pod per service, or the one pod of decision 20.

| # | Command | Expected |
| --- | --- | --- |
| 0 | `grep -E '^(MemTotal\|MemAvailable):' /proc/meminfo` and `docker ps --format '{{.Names}}'`, repeated just before row 10 | `MemAvailable` at least 12,189,696 kB (the 10,880 MiB of the `ci` preset plus 1,024 MiB for the harness, its fake pool workers and the port-forwards), else the run stops and records the figure without touching other containers |
| 1 | `uv sync --frozen` then `uv lock --check` | exit 0 for both, lock unchanged |
| 2 | `for m in wdf_workflow.gen wdf_runtime.gen wdf_events.schemagen wdf_jobdoc.schemagen wdf_artifactclient.gen wdf_artifactindex.gen wdf_eventstore.gen wdf_jobstate.gen wdf_stateops.gen wdf_storecheck.gen wdf_compat.vectors wdf_jobexamples.golden; do python -m $m --check \|\| echo "DRIFT $m"; done` | no output |
| 3 | `for c in activity temporal; do for f in contracts/$c/vectors/*/valid/*; do contracts/$c/check.sh "$f"; done; done`, the same over every file under `contracts/{activity,temporal}/vectors/*/invalid/` | exit 0 for each valid file, exit 1 with the `reason:` of its directory for each invalid one |
| 4 | `scripts/check-contracts-map.sh`, `scripts/export-diagrams.sh --check`, `just versions --check`, then the two refined diagrams opened | exit 0 for each, and the DAG diagram shows the four refinements of 0.2 (issue), checked by eye because an export gives every file one time and the diagram check compares times |
| 5 | `pytest packages/workflow -q -rs`, then `pytest packages/workflow -m isolated -q -rs` | all pass with no `SKIPPED` line, the second run covering the tests that the first deselects |
| 6 | `pytest packages/workflow -v -k "lost_completion_job_finishes or zombie_fenced or schedule_to_start_replaces or scheduler_down_beyond_task_timeout or history_budget_ends_job or deterministic_class_not_retried or infra_cap_exhausted or operator_interrupt_free or repeated_oom_killed_stops or drain_then_pause or resume_from_checkpoint or resume_compile_failure_stays_paused or corrupt_checkpoint_falls_back or manual_pause_interrupts_checkpoint_task or manual_pause_keeps_final_checkpoint or manual_pause_drains_others or manual_pause_queued_task_returns_ready or manual_pause_no_replace or lost_terminal_completes or unexpected_lost_resends or checkpoint_indexed_and_committed or index_checkpoint_refuses_other_attempt or resume_and_cancel_same_seq_cancel_wins or cache_hit_skips or cache_error_not_miss or stale_invalidate_keeps_fresh_entry or cache_invalidate_races_fresh_record or build_info_key_mismatch_refused or emit_retry_with_shifted_time_inserts_nothing or aggregate_waits_for_all or allow_partial_partial_status or aggregate_only_job or cancel_in_flight or terminate_hard_stop or signal_dedup or stage_contract_workflow or failure_table_covers or pinned_finishes_on_old_build"` | each named test is listed `PASSED` |
| 7 | `pytest packages/temporal packages/workflow -v -k replay` | all pass, including `test_replay_detects_reordered_activity` and the replay of `packages/workflow/corpus/v1/` |
| 8 | `pytest packages/workflow -v -m slow -k cache_key_matches_build_info` | `PASSED` |
| 9 | `pytest deploy/temporal -v` | the render tests pass, showing both cache keys, the limits of S10 and the roll on a changed key |
| 10 | `just up`, `scripts/tilt/run.sh ci`, `kubectl --context kind-wdf -n wdf-infra get pods`, `kubectl --context kind-wdf -n wdf-control logs deploy/workflow-worker-exit-a` | exit 0 with `appdb`, `appdb-migrate`, `objectstore`, `objectstore-bootstrap`, `mirror`, `temporaldb`, `temporal`, `temporal-bootstrap` and `workflow-worker` ready, the Temporal pods listed, and the log showing the workflow, control and reaper workers polling with build id `exit-a` |
| 11 | `H describe` | `namespace: wdf`, `retention: 24h`, the five `search_attribute:` lines with their types, and `schedule: wdf-artifact-reaper` |
| 12 | `cat /sys/fs/cgroup/memory.events` through `kubectl exec` in each Temporal pod, `H smoke --jobs 2000`, `curl -s http://127.0.0.1:<mport>/metrics` filtered for the cache series that S10 recorded, then the `memory.events` reads again | `finished: 2000`, `failed: 0`, the mutable state cache series at most 1,000, and the `max` counter of every Temporal pod unchanged |
| 13 | `H smoke --scenario pause-resume --jobs 20` | `finished: 20`, `failed: 0`, `paused_resumed: 20` |
| 14 | `H smoke --scenario zombie --jobs 20` | `finished: 20`, `failed: 0`, `zombie_commits: 0` |
| 15 | `H restart --jobs 200 --target <the Temporal pods>,workflow-worker --scenario worker-death --deaths 5` | `finished: 200`, `failed: 0`, and `infra_retries:` at least 5 |
| 16 | `WDF_BUILD_ID=exit-b H rollout` | `old_build: exit-a`, `new_build: exit-b`, `open_job_build: exit-a`, `new_job_build: exit-b`, and `drained: exit-a` before its Deployment is deleted |
| 17 | `just doctor`, then `just down`, then `just nuke` twice | exit 0 for each, and `just nuke` leaves nothing and repeats cleanly |
| 18 | `just ci` | exit 0, on the full path |
| 19 | the `ci` and `state-modules` workflows on `main` | one green run of each, the URLs recorded in Closure |

Not provable by this epic: the real placement service, pool table and OOM escalation (#9), real pool workers with the runner, the warm-up, exit 137 with node events and heartbeats from a guest (#10), the checkpoint format, segments, continue-as-new and kill-and-resume (#18), the admission start call and slot release (#8), signals through the control API and its outbox (#24), dashboards and alerts (#13), the standard load of 100,000 jobs at 1 GiB on the deployed Temporal (#16), engine and database outages longer than the heartbeat timeout (#15), a rollout with an old build kept for a month and two builds beside dispatch and admission on the infra node (#16), a pause longer than the retention safety net, FIFO order across the partitions of a pool queue, and the aggregate over 100,000 results.

## 8. Parallelism and cost

- Maximum parallel workers: 3 in the first phase (0.1, S9, and the D22 revisit followed by S10), 1 for 0.2, then 3 (P.1, A.1, M.1), then 4 (W.1, P.2, A.2, M.2), then 2 (W.2 and D.1, then W.3 and D.2), then 3 (W.4, V.2, V.3), then 1 for W.5, V.1 and the exit test.
- Host constraints: T1 work holds at most two containers across the host, and W, D and V tests hold one (the dev server).
  The cluster is single-owner, so S10, M.1, M.2, D.2 and the T2 halves of V.2, V.3 and the exit test queue for the lock, and `ci` needs 10,880 MiB of the 20 GiB host beside other containers.
- CI cost, estimates: no new workflow, two to three dev server containers started one after another per `just itest` session, and the replay corpus adds seconds.
- Estimates, not measurements, in worker-days: lane 0 (4: 0.1 2.5, 0.2 1.5), the D22 revisit (0.5), S9 (1.5), S10 (2), M.1 (2.5), M.2 (1.5), P.1 (2.5), P.2 (2), A.1 (2.5), A.2 (3.5), W.1 (3), W.2 (4), W.3 (2), W.4 (3.5), W.5 (3.5), D.1 (2), D.2 (3), V.1 (2), V.2 (3), V.3 (3), exit test (2.5).
  That is about 54 worker-days of effort and about 27 days elapsed along lane 0, P.1, W.1 to W.5, V.1 and the exit test (4 + 2.5 + 3 + 4 + 2 + 3.5 + 3.5 + 2 + 2.5), with S10 failing into the fallback of decision 20, the chart's per-service footprint, the D22 revisit, the SDK's raw versioning calls and cluster lock contention as the main sources of slip.

## 9. Closure (filled at close)

Closure addenda, one dated line each, added after close when a `revisit` child issue is filed against this epic:

- none yet
