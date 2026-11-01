# State contract

Owner epic: #3 (durable state and artifact storage).
Version: 1
Consumers: dispatch (#24), admission (#8), workflow (#6), broker (#7), artifacts (#12), observability (#13) and the characterization and soak epics (#16, #17).

This directory pins the shape of the three table families of the platform, the retention windows of the safety net and the loader of those windows.
The row models live in `packages/jobstate/` (`JobRow`), `packages/eventstore/` (`EventRow`) and `packages/artifactindex/` (`ArtifactRow`), and their exported schemas under [schemas/](schemas/) are generated from them.
`python -m wdf_jobstate.gen --check`, `python -m wdf_eventstore.gen --check` and `python -m wdf_artifactindex.gen --check` fail when a committed schema or [retention.schema.json](retention.schema.json) differs from its model.
The DDL belongs to the lanes that own each table, and a conformance test in each package holds it to the tables below.
Temporal owns workflow lifecycle, and nothing here decides it.

## Attempt rule

The attempt number is the workflow's own per-task attempt counter, monotonic across retries, resumes and patches.
The store never infers an attempt: it never defaults, counts or increments one.
Every attempt column holds what the event or the artifact reference carries, has no `DEFAULT`, and a test asserts it.

## Table `accepted_job`

One row per job, the dedup point and the control record.
`unique (tenant, submission_key)` is the one dedup point, and no retention rule ever touches this table.
A column marked generated or set by the database is never written by a caller.

| Column | Type | Rule |
| --- | --- | --- |
| `job_id` | `uuid` | Primary key, the value of `wdf_keys.job_id(tenant, submission_key)` |
| `tenant` | `text` | Not null |
| `submission_key` | `text` | Not null, at most 256 characters |
| `batch_id` | `uuid` | Not null |
| `priority_class` | `text` | Not null |
| `job_class` | `text` | Default `short`, checked against `classes` of `contracts/job/limits.yaml` |
| `status` | `text` | Default `accepted`, checked against the statuses of `contracts/status/status.yaml` |
| `manifest_ref` | `jsonb` | The artifact reference of kind `manifest` |
| `manifest_sha256` | `text` | Generated from `manifest_ref` |
| `schema_version` | `text` | The `dataschema` of the accepted event |
| `traceparent` | `text` | Not null |
| `tracestate` | `text` | Null when the event carries none |
| `published_at` | `timestamptz` | Null until the broker confirmed the publish |
| `seed` | `bigint` | From 0 up to below 2^53 |
| `parameters` | `jsonb` | Checked to be an object |
| `fingerprint` | `text` | 64 lower-case hex characters |
| `requested_action` | `text` | Null, or one of `pause`, `resume`, `cancel` and `terminate` |
| `control_seq` | `integer` | Default 0 |
| `control_delivered_at` | `timestamptz` | Null until the request was delivered |
| `pending_patch_ref` | `jsonb` | Null, or the artifact reference of a pending patch |
| `created_at` | `timestamptz` | Set by the database |
| `updated_at` | `timestamptz` | Set by the database on every change |
| `terminal_at` | `timestamptz` | Null, set by the guarded move into a terminal status |

The class of a job is declared by the submitter and defaults to `short`.
The submit path writes it once to `job_class` and sets the `jobclass` attribute of the accepted event to the same value.
`pausing` and `cancelling` are views over an outstanding requested action and are never stored.

## Table `event`

The append-only event history, a hypertable on `event_time`.
`unique (event_id, event_time)` is the idempotency key, and the chunk interval is one day.

| Column | Type | Rule |
| --- | --- | --- |
| `event_id` | `text` | `<source>#<id>` |
| `event_time` | `timestamptz` | The `time` of the event, with microseconds |
| `type` | `text` | Not null |
| `source` | `text` | Not null |
| `tenant` | `text` | Not null |
| `job_id` | `uuid` | Not null |
| `task_id` | `text` | Null for an event of the job |
| `attempt` | `integer` | Null for an event with no attempt, and no default |
| `body` | `text` | The canonical bytes of `wdf_events.to_event_row`, never `jsonb`, which reorders keys |

## Table `artifact`

One table holds the index of references and the compile cache entries, so a cache entry is a row and not a second store.
A reference has `uri` and `sha256` set and the cache columns null.
A cache entry has `kind = 'compile-cache'`, the cache columns set, `uri` null and the three producer columns null.
The producer columns are null exactly for the content-addressed kinds, and set for the attempt-scoped kinds of `contracts/artifact/layout.yaml`.

| Column | Type | Rule |
| --- | --- | --- |
| `row_id` | `bigint` | Generated always as identity, the primary key |
| `kind` | `text` | A kind of `contracts/artifact/layout.yaml`, or `compile-cache` |
| `uri` | `text` | Unique, null for a cache entry |
| `bucket` | `text` | The bucket of the kind |
| `key` | `text` | The key within the bucket |
| `sha256` | `text` | 64 lower-case hex characters |
| `size` | `bigint` | At least 0 |
| `name` | `text` | The reference name, no slash |
| `producer_job` | `uuid` | The attempt identity as the reference carries it |
| `producer_task` | `text` | The attempt identity as the reference carries it |
| `producer_attempt` | `text` | Matches `^r[0-9]+a[0-9]+$`, stored as given, and no default |
| `created_at` | `timestamptz` | Set by the database |
| `deleted_at` | `timestamptz` | Null while the reference is live |
| `cache_key` | `text` | 64 hex characters, a partial unique index where `kind = 'compile-cache'` |
| `wasm_uri` | `text` | References `artifact (uri)` with no cascade |
| `componentize_py_version` | `text` | Null unless a cache entry |
| `wit_version` | `text` | Null unless a cache entry |
| `last_used_at` | `timestamptz` | Null unless a cache entry |

A cache entry counts as a reference to its wasm.
The foreign key refuses a raw delete of that row, and the library refuses to soft delete a wasm that a live entry names.

## Writers

One writer per kind of change.
The libraries below are the only code that issues these statements, and the grants of the group roles enforce it at table and column level.
A status value cannot be restricted by a grant, so the trigger and the library keep that rule.

| Change | Writer | Statement |
| --- | --- | --- |
| Insert the row and its accepted event, then set the receipt | the submit sequence of the dispatch API, role `wdf_dispatch` | `insert_accepted`, `insert_events`, `mark_published` |
| Request cancel, pause, resume or terminate, and a row-only move of a job that never started | the control API, role `wdf_control` | `request_action`, `mark_delivered`, `transition(..., row_only=True)` and their events |
| Claim a job | admission, role `wdf_admission` | `transition(accepted, admitted)` and `insert_events` for the admitted event |
| Project running, paused and the terminal statuses | the workflow, role `wdf_workflow` | `transition` and `insert_events` for every event except submit, control and admission events |
| Index a committed reference and a cache entry | the artifact client, role `wdf_artifacts` | `index_reference`, `record_cache_entry`, `touch_cache_entry` |
| Soft delete an expired reference | the reaper, role `wdf_reaper` | `mark_deleted` |

## Retention

[retention.yaml](retention.yaml) holds two blocks, keyed by the table `event` or by a bucket name.
`windows` holds the rules that drop or expire data, and `housekeeping` holds the rules that remove nothing a job can still need.
The event history can only be cut by time, because a hypertable policy drops whole chunks, and a bucket rule can only expire by age, so neither keeps a failed job longer than a succeeded one.
That choice stays with the reaper of the artifact epic.

- Every entry of `windows` is at least twice `MAX_JOB_DURATION_DAYS` of `wdf_compat.window`, which covers a full length run followed by a pause of the same length.
- The events are dropped after 14 days, results expire after 28 days and checkpoints after 14 days.
- Events are compressed after 2 days and incomplete multipart uploads are aborted after 1 day, and neither has a floor.
- Sources and components have no age rule, because their keys are content-addressed and shared across jobs.
- The table `accepted_job` has no retention of any kind.
- A pause longer than the checkpoint window is outside the proof of concept.

[check.sh](check.sh) validates one file and exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr and 2 on a usage error.
The reasons are `schema-violation`, `window-below-twice-max-job-duration`, `retention-on-job-rows` and `age-rule-on-content-addressed`.
Vectors are under [vectors/retention/](vectors/retention/), and the file of each invalid vector sits in the directory named by its reason.

## Changing the contract

A change to a model, a window or a column table after the first merge is a contract-change PR with a consumer list and a bump of `VERSION`.
That PR carries the contract file, the model under `packages/` and the regenerated output together, because the drift check fails otherwise.
A lane that adds a file to this directory adds its own table row here in the PR that adds the file.
