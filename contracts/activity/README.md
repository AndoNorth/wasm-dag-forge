# Activity contract

Owner epic: #6 (workflow engine on Temporal).
Version: 1, in [VERSION](VERSION).
The [Consumers](#consumers) section names who reads each part.

The workflow runs each task as one activity, `run_task`, on the queue of its pool.
This directory pins what crosses that boundary: the task input, the task result, the failure details, the heartbeat details and the failure class table that decides what a failure costs.
The schemas are generated from the models of `wdf_workflow.models` and never edited by hand.

## Payloads

A payload is canonical JSON, as `wdf_temporal.converter` writes a model: compact, sorted keys, UTF-8.
A size bound counts those bytes.
A time is a string of `wdf_events.models.TIME_PATTERN`, and every reference reuses a model of the events contract, so no new reference shape exists.
`LogTailReference` is `wdf_events.models.AttemptReference` narrowed to kind `log-tail`, as `ResultReference` narrows it.
A payload holds references and never bytes.

### Task input

`TaskInput` is the one argument of `run_task`, at most 262,144 bytes, with schema [task-input.schema.json](task-input.schema.json).

| Field | Type | Rule | Reuses |
| --- | --- | --- | --- |
| `request` | object | a `dev.wdf.task.requested.v1` event that `wdf_events.check_task_request` accepts, written as `wdf_events.to_bytes` writes it, with no `attempt` attribute, because the envelope refuses one on that type | `wdf_events.Event`, data `TaskRequestData` |
| `task_attempt` | integer | at least 1 | the task attempt of the events contract |
| `resume_counter` | integer | at least 0 | the second input of `wdf_keys.attempt_identity` |
| `batch_id` | string | a UUID | the `<batch>` segment of every attempt-scoped key |
| `seed` | integer | 0 to 2^53 - 1 | `JobParams.seed`, bounded above by `wdf_jobdoc.models.SEED_LIMIT`, the `<seed>` segment of every attempt-scoped key |
| `params` | object | the `params` of the bound plan task for `request.data.task_type`, with no placeholder | `wdf_jobdoc.models.CompileParams`, `wdf_runtime.models.Input`, the aggregate params of `wdf_jobdoc.models` with string lists or null in place of a placeholder |
| `cache_key` | string or null | 64 lower-case hex digits on a compile task with a key, null otherwise | `wdf_keys.compile_cache_key` |

`request.data.inputs` holds no reference for compile, exactly one `component` for simulate, and exactly one `index` for aggregate (the index the workflow builds, or the submitter's `params.index`), and the worker reads the index from `inputs`.
The worker's producer is `ProducerRef(job=request.jobid, task=request.taskid, attempt=attempt_identity(task_attempt, resume_counter))`.
The worker writes `params` as `/in/input.json`, because the task request carries none.
A compile task has no key when its bundle has no `uv.lock` or its modules lack the entry, and the worker then fails it as `compile_failed`.

The converter writes the request inside the payload as exactly the bytes of `wdf_events.to_bytes`, so the history holds the bytes that the event store holds.

### Task result

`TaskResult` is the result of a successful `run_task`, at most 4,096 bytes, with schema [task-result.schema.json](task-result.schema.json).

| Field | Rule |
| --- | --- |
| `outputs` | one or two references whose kinds are, in order, the declared outputs of one task type of `contracts/artifact/staging.yaml`: compile `component` and `build-info`, simulate `result`, aggregate `summary` |
| `log_tail` | a `LogTailReference` |
| `started_at` | the time the worker started the attempt, which the workflow writes as the time of `task.started` |

### Failure details

A worker reports a failure as an `ApplicationError` whose `type` is a row of the failure class table with source `workload`, `artifact` or `runner`, and whose details are `FailureDetails`, at most 4,096 bytes, with schema [failure-details.schema.json](failure-details.schema.json).

| Field | Type | Rule | Reuses |
| --- | --- | --- | --- |
| `deterministic` | boolean | the row's value, or either value on a `from-error-file` row | `wdf_runtime.classify.Classified.deterministic` |
| `reason` | string | 1 to 1,024 characters, starting with the class and `: ` | `Classified.reason`, `ArtifactError.reason` |
| `started_at` | string | a time | |
| `checkpoint` | object or null | the latest checkpoint the attempt uploaded | `wdf_events.models.CheckpointReference` |
| `log_tail` | object or null | null only when the upload of the tail itself failed | `LogTailReference` |

A `failure-details` vector is the pair `{type, details}` that a worker raises, so the loader checks the details against the row of their type.

### Heartbeat details

`HeartbeatDetails` are the details of each `run_task` heartbeat, at most 2,048 bytes, with schema [heartbeat.schema.json](heartbeat.schema.json).
`progress` (0 to 1), `step` (0 to `steps`) and `steps` (at least 1) come from the guest's `HEARTBEAT` line as `wdf_runtime.contract.heartbeat` parses it, and all three are null during staging, the warm-up of a component's first run on a pod, or on a pool without guest heartbeats.
`checkpoint` is the latest checkpoint reference or null, and `started_at` is the time the attempt started.
The latest checkpoint reference is in both the heartbeat and the failure details, so a worker that dies still hands over its last reference through its last heartbeat.

## Failure class table

[failure-classes.yaml](failure-classes.yaml) has one row per class, with schema [failure-classes.schema.json](failure-classes.schema.json).

| Column | Values | Rule |
| --- | --- | --- |
| `class` | string | the class or code as its source file spells it |
| `source` | `workload`, `artifact`, `runner`, `temporal`, `placement` | the worker reports the first three, and the workflow decides `temporal` from the timeout type and `placement` from an `Unschedulable` verdict |
| `pool` | `compile`, `simulate`, `aggregate` or absent | set exactly on a `workload` row, the pool `contracts/workload/exit-classes.yaml` lists the class under |
| `deterministic` | `true`, `false`, `from-error-file` | a workload row copies `exit-classes.yaml`, where `from-error-file` takes the value of the failure details; an artifact row is `false` when its `attempt_class` in `contracts/artifact/failures.yaml` is `retryable` or its retry rule is `fallback-to-previous-checkpoint`, and `true` otherwise |
| `budget` | `guest`, `infra`, `free`, `none` | `guest` and `infra` consume one attempt of that budget; `free` consumes nothing and the task runs again, or for `operator_interrupt` waits for the resume; `none` ends the task at once as an exhausted budget, and the plan's `checkpoint` flag of the task then decides between pausing and failing the job |
| `action` | absent, `invalidate-cache-and-rebuild`, `previous-checkpoint`, `re-place`, `escalate-once` | an action replaces the budget rule for the occurrence it applies to |

| Action | Rule |
| --- | --- |
| `invalidate-cache-and-rebuild` | applies only when the lost object is the component of a compile cache hit of this job: the workflow looks the key up again, invalidates only an entry that still names the lost component, places a new compile task, and runs the consumer again consuming nothing, and a second loss in the same run ends the task |
| `previous-checkpoint` | the next attempt resumes from the checkpoint behind the head of the two the workflow keeps, and when none is left the failure becomes `ARTIFACT_LOST` |
| `re-place` | a new `place` call with the schedule-to-start timeout doubled, up to the ceiling of `contracts/temporal/pools.yaml` |
| `escalate-once` | the next attempt runs once in a larger memory class when the placer offers one; pool table v0 offers none, so the guest budget alone applies until the placement epic adds classes |

The rows are every class of `exit-classes.yaml` except `ok`, which is a success, every code of `failures.yaml`, and the runner, Temporal and placement classes that this table is the source of.

| Class | Source | Pool | Deterministic | Budget | Action |
| --- | --- | --- | --- | --- | --- |
| `timeout` | workload | simulate | false | guest | |
| `guest_memory_cap` | workload | simulate | true | none | |
| `guest_failed` | workload | simulate | from-error-file | guest, or none when the details say deterministic | |
| `host_error` | workload | simulate | false | infra | |
| `oom_killed` | workload | simulate | false | infra, and two in a row of one task stop the retry | |
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

`ARTIFACT_NOT_FOUND` and `ARTIFACT_CORRUPT` escalate to `ARTIFACT_LOST` inside the artifact client after its refetch, so a report of either means the refetch did not run, and the row ends the task.
`compile_dependency_unavailable` consumes the infrastructure budget because the package mirror is the platform's, and `aggregate_failed` the guest budget because the aggregate library is the task's own code.
`oom_killed` of the simulate pool consumes the infrastructure budget, because a kill while the guest is below its own cap is a platform sizing fault.
The guest's own run timeout (`timeout`, `compile_timeout`, `aggregate_timeout`) consumes the guest budget, and a Temporal start-to-close or heartbeat timeout the infrastructure budget, because it means the worker did not report.

## Consumers

| Consumer | Reads |
| --- | --- |
| #6 workflow engine | every part: it builds the task input, reads the result and the details, and applies the table |
| #9 placement | the placement classes, and `escalate-once`, which a larger memory class answers |
| #10 Kubernetes workers | the task input, the task result, the failure details, the heartbeat details and the classes a worker reports |
| #13 observability | the classes and budgets, as labels of the workflow's retry metrics |
| #15 failure scenarios | the table, to name the failure each scenario provokes |
| #18 checkpoint and resume | the checkpoint references in the heartbeat and failure details, and `previous-checkpoint` |

## Loader

`contracts/activity/check.sh <file> [--as NAME]` runs `python -m wdf_workflow.contract --check <file> [--as NAME]`, the loader of both this contract and the Temporal contract.
`NAME` is a schema: `task-input`, `task-result`, `failure-details`, `heartbeat` or `failure-classes`.
Without `--as`, the name is the directory under `vectors/` that holds the file, or else the file's stem, so `failure-classes.yaml` is checked as `failure-classes`.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.
The loader reads `exit-classes.yaml`, `failures.yaml`, `staging.yaml` and the pinned table through the package data of `wdf_workflow`, and never through a repository path.
`python -m wdf_workflow.gen [--check]` writes the generated schemas of both contracts, and with `--check` exits 1 and names each committed schema that differs from the models.

The first failing check wins, in this order.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match its model, which includes an unknown budget or action, a row named `ok`, a workload row without a pool, a pool on any other row, a step above steps and a partial progress triple |
| `too-large` | a payload is above its bound |
| `bad-request` | `check_task_request` refuses the request, or the request carries `attempt` |
| `params-type-mismatch` | `params` is not the shape of the request's task type |
| `cache-key-mismatch` | `cache_key` is set on a task that is not compile |
| `inputs-mismatch` | the request inputs break the per-type rule of the task input |
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
| `class-not-covered` | a class of `exit-classes.yaml` other than `ok` or a code of `failures.yaml` has no row |

`vectors/<name>/{valid,invalid/<reason>}/` hold the vectors, and `packages/workflow/tests/test_loader.py` runs them inside `just ci`.
Each `failure-classes` vector is the pinned table with one change.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
A new class in `exit-classes.yaml` or a new code in `failures.yaml` fails `class-not-covered` until this table has its row, so a change there comes with a row here.
