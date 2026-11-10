# Temporal contract

Owner epic: #6 (workflow engine on Temporal).
Version: 1, in [VERSION](VERSION).
The [Consumers](#consumers) section names who reads each part.

Temporal owns the state of every job.
This directory pins the names that the workflow, its workers and its callers share, pool table v0, the control signals and the status query.
The schemas are generated from the models of `wdf_workflow.models` and never edited by hand.
The payload of each signal and query is canonical JSON, as `wdf_temporal.converter` writes a model.

## Naming

[naming.yaml](naming.yaml), with schema [naming.schema.json](naming.schema.json).

| Key | v1 value | Rule |
| --- | --- | --- |
| `namespace` | `wdf` | retention 1 day, set by the Temporal bootstrap |
| `workflow` | type `JobWorkflow`, queue `wdf-workflow`, id `wdf_keys.workflow_id(job_id)` with reuse policy `REJECT_DUPLICATE`, versioning behaviour `PINNED`, deployment `wdf-workflow-worker` with the build id of `WDF_BUILD_ID` | the only versioned queue, so every job finishes on the build that started it |
| `control_queue` | `wdf-control-<build id>`, with the build id of `WDF_BUILD_ID` | one control queue per build, unversioned, served only by that build's worker service |
| `pools` | `compile: wdf-compile`, `simulate: wdf-simulate`, `aggregate: wdf-aggregate` | the keys equal the pools of `contracts/job/registry.json`, and every pool queue is unversioned |
| `activities.pool` | `run_task`, a single attempt, cancellation type `WAIT_CANCELLATION_COMPLETED` | the workflow runs the retry loop, and a task leaves `running` only when its activity returns |
| `activities.control` | `load_plan`, `cache_lookup`, `cache_record`, `cache_invalidate`, `commit`, `index_checkpoint`, `build_index`, each with at most 5 attempts and backoff from 1 s doubling to 60 s | regular activities on the control queue |
| `activities.local` | `place` and `emit`, each with no attempt limit and backoff from 1 s doubling to 600 s | local activities, so a database or scheduler outage stalls a job and fails nothing |
| `signals` and `queries` | `cancel`, `terminate`, `pause`, `resume`; `status` | the signal and query shapes below |
| `search_attributes` | `WdfTenant`, `WdfPriorityClass`, `WdfJobClass` and `WdfJobId` (keyword) and `WdfSubmittedAt` (datetime) | set by admission at the start |
| `schedules.artifact_reaper` | id `wdf-artifact-reaper`, queue `wdf-artifact-reaper`, workflow `ReapWorkflow`, workflow id `wdf-artifact-reaper-pass`, activity `reap_pass` | as `wdf_artifactreaper.schedule` delivers them, created in namespace `wdf` |

A retry policy is written with Temporal's own fields, `maximum_attempts`, `initial_interval_s`, `backoff_coefficient` and `maximum_interval_s`, where `maximum_attempts` 0 means no limit, as in Temporal.
The estimates among them are fixed by the performance epic.

## Pool table v0

[pools.yaml](pools.yaml), with schema [pools.schema.json](pools.schema.json), marked `estimate: true`.
The placement epic replaces it with real values and timeout formulas by a contract-change PR, and keeps the queue names of `naming.yaml`.

| Pool | `cpu_milli` | `memory_mib` | `schedule_to_start_s` | `heartbeat_s` | `start_to_close_margin_s` |
| --- | --- | --- | --- | --- | --- |
| `compile` | 1000 | 1024 | 1800 | 60 | 120 |
| `simulate` | 250 | 384 | 1800 | 60 | 120 |
| `aggregate` | 1000 | 1024 | 1800 | 60 | 120 |

The slots equal `deploy/presets/slots/ci.yaml`, and `packages/workflow/tests/test_pools.py` holds them equal.
A placer takes the queue of a pool from `pools` of `naming.yaml`, so the queue names live in one file.
The start-to-close timeout of a task is its run timeout plus the margin, because `run_timeout_s` is a field of each plan task.
The heartbeat timeout of 60 s is above the 30 s that a component's first run on a pod takes to warm up, and the margin of 120 s covers that warm-up and staging.
The schedule-to-start timeout starts at `schedule_to_start_s` and doubles at each re-schedule up to `schedule_to_start_ceiling_s`, 43,200 s, with no count cap, so a task is never dropped.
A request above the slot of its pool is `insufficient_memory` or `insufficient_cpu`, and a task type with no pool is `no_matching_pool`.

## Signals and query

`ControlSignal`, the argument of `cancel`, `terminate` and `pause`, is `{control_seq, reason}`, with schema [control-signal.schema.json](control-signal.schema.json).
`control_seq` is an integer from 1 to 2,147,483,647, the job row's `control_seq`, and `reason` is a string of at most 1,024 characters or null, the reason of the control request.
`ResumeSignal`, the argument of `resume`, is `{control_seq, manifest}`, with schema [resume-signal.schema.json](resume-signal.schema.json), where `manifest` is the row's `pending_patch_ref` as a `wdf_events.models.ManifestReference`, or null without a patch.
`pause` and `resume` share one last-applied `control_seq`, and one whose `control_seq` is not above it is ignored.
`cancel` and `terminate` are applied whenever they arrive unless the job has ended, and a repeat does nothing.
A caller sends `resume` only while the row's `requested_action` is `resume`, with the row's `control_seq`.

`StatusQuery`, the result of the query `status`, at most 524,288 bytes, with schema [status-query.schema.json](status-query.schema.json).

| Field | Rule |
| --- | --- |
| `job_status` | a status of `contracts/status/status.yaml` |
| `pause_reason` | `operator`, `retries-exhausted` or `resume-compile-failed`, set exactly when `job_status` is `paused` |
| `resume_counter` | at least 0 |
| `control_seq` | the last applied `control_seq` of `pause` and `resume`, 0 before the first, which lets a caller confirm delivery |
| `tasks` | at least one entry, in ascending order of the task id string, each with `id`, `state`, `task_attempt` (0 before the first run, which is how the query lists the tasks that never ran), `guest_left`, `infra_left`, `reschedules`, `last_class` (a row of `contracts/activity/failure-classes.yaml` or null) and `checkpoint` (the newest checkpoint the workflow keeps for the task, or null) |

A task `state` is `pending`, `ready`, `running`, `succeeded`, `skipped`, `failed`, `blocked`, `paused` or `cancelled`.
`running` means an activity is scheduled on a pool queue, started or not, because the workflow cannot see a start, and `blocked` means a dependency failed or paused.
`skipped` means a compile task that a cache hit satisfied (`task.skipped` with reason `compile-cache-hit`), and it counts as succeeded for its dependents, the job result and a resume.
The `skipped_tasks` of `job.partial` are another set: every task that is neither `succeeded` nor `skipped` when the job ends.
The schema bounds the query by its 524,288 bytes, and the `max_tasks` of the plan's limits profile bounds the number of tasks.

## Consumers

| Consumer | Reads |
| --- | --- |
| #6 workflow engine | every part |
| #8 admission | the workflow type, queue, id, reuse policy and search attributes of the start, and the resume signal |
| #9 placement | the pool queues and pool table v0, which it replaces |
| #10 Kubernetes workers | the pool queues and the pool activity, which every worker serves unversioned |
| #12 artifact passing | the reaper Schedule's names |
| #13 observability | the namespace, queues and status query |
| #15 failure scenarios and #16 performance | the names and pool table v0 the harness drives |
| #24 dispatch and control API | the control signals and the status query |

## Loader

`contracts/temporal/check.sh <file> [--as NAME]` runs `python -m wdf_workflow.contract --check <file> [--as NAME]`, the loader of both this contract and the activity contract.
`NAME` is a schema: `naming`, `pools`, `control-signal`, `resume-signal` or `status-query`.
Without `--as`, the name is the directory under `vectors/` that holds the file, or else the file's stem, so `naming.yaml` is checked as `naming`.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.
The loader reads `registry.json`, `status.yaml` and the pinned failure class table through the package data of `wdf_workflow`.

The first failing check wins, in this order.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match its model, which includes a pool outside the registry's three names, a pool activity with more than one attempt, a control activity with no attempt limit, a local activity with one, and a first schedule-to-start timeout above the ceiling |
| `too-large` | a status query is above its bound |
| `duplicate-name` | a name is used twice across the queues, the activities, the signals and the query, where `run_task` on its three pool queues counts once |
| `pool-mismatch` | the pools of `naming.yaml` or `pools.yaml` differ from the registry |
| `unknown-status` | `job_status` is not a stored status of `status.yaml` |
| `unknown-class` | a task's `last_class` is not a row of the failure class table |
| `pause-reason-mismatch` | `pause_reason` is set while the job is not paused, or null while it is |
| `out-of-order` | the task ids are not in strictly ascending string order |

`vectors/<name>/{valid,invalid/<reason>}/` hold the vectors, and `packages/workflow/tests/test_loader.py` runs them inside `just ci`.
`naming` and `pools` have their pinned file as their valid file, and each of their invalid vectors is the pinned file with one change.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
A queue, workflow or activity name changes only with a rollout plan, because a running job is pinned to the build that started it.
