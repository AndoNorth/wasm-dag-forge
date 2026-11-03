# Epic #12: Artifact passing - breakdown

Status: draft
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/12
Architecture note: docs/architecture/13-state-envelope-artifacts.drawio
Depends on (closed epics): Dev environment (#1), Fixed-resource cluster (#2), Job event envelope (#4), DAG configuration schema and validator (#5), Durable state and artifact storage (#3)

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-11-03) while preparing this spec, `[read]` means read and not re-run.
The epic issue body was read on 2026-11-03, and this spec was written against that version.
`main` was at `9c7e1c4`, the merge that closed the state and storage epic.
Method: [docs/delivery/method.md](../method.md).
Models: [docs/delivery/epics/05-state-storage.md](05-state-storage.md) (the most recent, fully delivered breakdown, whose structure this spec follows), [docs/delivery/epics/04-validator.md](04-validator.md), [docs/delivery/epics/03-envelope.md](03-envelope.md) and [docs/delivery/epics/02-cluster.md](02-cluster.md).
Spike results consulted: [object store](../spikes/object-store.md) (S5) for the product facts, [state footprint](../spikes/S8/state-footprint.md) (S8) for the 100,000 object figures and the heap bound, and [Temporal](../spikes/temporal.md) (S3, S3b) for the payload limit and the retry-policy finding.
Tiers, as the earlier breakdowns use them: T0 is host Python with no runtime, T1 is containers started through `wdf_itest` (at most two containers across the host, one at a time per session), and T2 is the cluster from `just up` on preset `ci` under `/tmp/wdf-cluster.lock`.
A test that needs a container carries the `integration` marker and so runs in `just itest` and in every lane's `just ci`.
A T2 check is a command and never a pytest test, so `just ci` never needs a cluster.

## 0. Decisions for the captain

The method caps this section at five questions.
The brief asked for every open question of the epic to be carried here, so there are six of them (0.2) and five new decisions found while reconciling the epic body with what is on `main` (0.3).
Each has a default that the lanes build on, so none blocks the start, and an answer that differs from the default changes only the issues named in the last column.

### 0.1 Recorded decisions

Everything in the epic body stands except where merged work has overtaken it, which section 1 lists.
The calls below were made for earlier epics or by the brief that asked for this spec, and are applied here without a new question.

| # | Decision | Source | Applied in |
| --- | --- | --- | --- |
| 1 | No verifier skill, no feature files and no verify recipes for this epic, and the `verify-storage` row of the method (`docs/delivery/method.md` lists it for #3 and #12) is dropped for #12 as it was for #3. The product tests stay: the schema drift checks, the contract loader vectors, the zombie, corruption and fallback tests, the retention safety tests, the race tests of the collector and the aggregate bench | the captain's standing rule, applied in the envelope, validator and state epics | sections 3, 4 and 6 |
| 2 | Python models are the source of anything with a schema, and the exported JSON Schemas are checked in with a drift test. `contracts/artifact/reference.schema.json` is hand written today and becomes generated from the models (R.1). The YAML contracts of lane 0 are validated by a loader whose models generate their schemas | the envelope epic, decision 5, carried | 0.3, R.1, 0.1, 0.2 |
| 3 | `just` stays the entrypoint with logic in scripts. This epic adds no recipe and no `just/*.just` module: tests run through `just test`, `just itest` and `just ci`, and cluster helpers are scripts under `scripts/artifacts/` | the dev environment epic, carried | sections 3 and 4 |
| 4 | Epics are local-development and declarative: no cloud service, no operator and no controller. The reaper is a Temporal Schedule that starts a workflow, and no cron job or controller exists beside it | the brief | P.3 |
| 5 | Runtime libraries read their contract files through package data (`importlib.resources`) and never by repository path, as `wdf_events` does with its `contract/` links. Every new package ships what it reads, and a wheel test imports and uses the built wheel from a clean virtual environment | state epic, decision 11, carried | 0.3, R.1, every package issue |
| 6 | General docs do not hard-code counts. The architecture note and `docs/dev-setup.md` name no number of kinds, codes, roles or lanes, and the counts in this spec belong to the spec | the brief | section 8 (Closure) |
| 7 | Epics 1 to 5 are merged and closed, so this spec consumes their real contracts, libraries and versions as pinned in section 2 | the brief | section 2 |
| 8 | The attempt number is the workflow's own per-task counter, never reused. The client never infers, counts or defaults an attempt: the producer of a reference and every segment of an attempt-scoped key come from the caller, and the library only checks them against the layout | state epic, decision 6, and the events contract | C.1, C.2 |
| 9 | The maximum job duration stays 7 days, and every window of this epic is derived from it by the floors that the loaders enforce. Scaling beyond 7 days is not work here | state epic, decision 7 | 0.2, P.1 |
| 10 | One content-addressed key space is shared across tenants, because the artifact contract has no tenant in `sha256/<hash2>/<hash>`. The existence side channel that follows belongs to this epic (question 2) | state epic, decision 13 | 0.2 question 2 |
| 11 | Compile cache entries are rows of the one artifact index table and there is no second table. The table, the partial unique index that makes the first writer win and the rule that a live entry pins its wasm are delivered (section 1) and are not rebuilt here | state epic, decision 18 | S.1, K.3 |
| 12 | The aggregate index is built by an activity of the workflow just before the aggregate task starts, from the result references that the workflow recorded, and an aggregate-only job takes an index object from its submitter (the epic body, stands). This epic delivers the format, the builder function and the reader, and the activity itself is the workflow epic's (#6) | the epic body | 0.2, K.2 |
| 13 | Guest I/O for checkpoints is a WASI directory, as the checkpoint epic (#18) defines it. This epic stores, verifies and prunes the checkpoint objects, and the input and output directories of a task are the staging contract | the epic body | 0.1, C.3, K.1 |
| 14 | The golden `job.accepted` vector class (issue 216) is a validator vocabulary matter and is not taken here | the brief | none |
| 15 | No new CI workflow. The `state-modules` workflow already deploys the store and runs the contract script, and its path filter already lists `contracts/store/**`, `contracts/state/**`, `packages/storecheck/**` and `packages/migrate/**`, which are the paths of the two issues that change the store and state contracts. The T2 evidence of this epic comes from the exit test | review of `.github/workflows/state-modules.yml` [read] | section 7 |

### 0.2 Open questions of the epic

| # | Question | Options | Recommendation | Default if no answer, and the consequence of the alternative | Changes |
| --- | --- | --- | --- | --- | --- |
| 1 | Which windows does the reaper use per terminal state, and does a paused job expire? The safety net of `contracts/state/retention.yaml` (results 28 days, checkpoints 14 days) is fixed and the reaper sets its own windows below it | (a) per status, counted from `terminal_at`: succeeded 7 days for results and 1 for checkpoints, partial and failed 14 and 3, cancelled and terminated 3 and 1, and no reaper rule for a paused job, which keeps its objects until the safety net, (b) one window for every terminal status, 14 days, (c) as (a) and a paused job expires after the checkpoint net by the reaper's own rule | (a). The epic keeps partial results of a failed job longer than a succeeded job's, and a succeeded job's checkpoints have no further use. A pause beyond the net is outside the proof of concept (the retention file says so), so nothing extra is needed for it | (a). With (b) a succeeded job's results are kept as long as a failed one's, which doubles the disk for no stated reason, and (c) puts a second expiry on paused jobs that the checkpoint epic owns | 0.2, P.1 |
| 2 | Is the compile cache global or per tenant, and what are its expiry window and size cap? | (a) global, expiry by last use after 14 days (twice the maximum job duration, a floor that the loader enforces so the entry of a running job never expires), no size cap, and the existence side channel accepted and written down, (b) per tenant, which adds a sixth input to `wdf_keys.compile_cache_key` by a contract-change PR, (c) global with a size cap in bytes of referenced wasm and least recently used eviction | (a). The key space of the blobs is already global (decision 10), the key is a hash of content so the entry is not a trust boundary, and the wasm under a key was built from exactly that source. The side channel is that a tenant whose compile is skipped learns that identical source was built before, which a single operator proof of concept can accept | (a). With (b) the keys contract moves to a new version with consumers #6 and #11, and identical sources are built once per tenant. With (c) an eviction rule exists that nothing needs yet, and the capacity alert of the state epic already covers the bucket | S.1, K.3, P.1 |
| 3 | Do tiny results (a digest per seed) need one object each, or may they sit in a Temporal payload or a Postgres row? | (a) one object per result, never inlined, (b) inline below a threshold in a row or a payload | (a). S8 stored 100,000 objects of 1 KiB in 371 MiB at 383 puts per second through 16 threads, with the store heap bounded and a working set peak of 533 MiB, so the count is carried. One code path is the epic's own key decision. The exit test measures a 100,000 result aggregate through the real client | (a). With (b) a second read path, a second retention path and a reference that is sometimes bytes exist, which reverses the key decision "values are never inlined" | K.2 |
| 4 | Should a result object carry an index of its block ids and sizes so a consumer can skip blocks? | (a) the whole object is always read (the epic's proposal), (b) a header index | (a). Block level checks belong to the consumer that parses the blocks, and one hash covers one object | (a). With (b) the object has a second format and a header that the hash does not separate from the data | none |
| 5 | What are the maximum single artifact size, checkpoint size and per-job storage cap? | (a) per object caps by kind in the staging contract (manifest 1 MiB, source bundle 64 MiB, index 128 MiB, component 64 MiB, build-info 1 MiB, result 64 MiB, output 256 MiB, summary 16 MiB, log tail 64 KiB, checkpoint 64 MiB per object) and no per-job cap, (b) as (a) with a per-job byte cap enforced by accounting, (c) larger checkpoint objects | (a). The checkpoint cap is chosen against the store claim of 10,240 MiB: the `ci` profile allows 32 parallel tasks and two kept checkpoints each, so 32 x 2 x 64 MiB is 4,096 MiB. The numbers are estimates and a change is a contract-change PR. The index cap covers 100,000 entries of about 380 bytes (38 MB) with room for three times that | (a). With (b) every attempt-scoped write needs accounting across attempts, which has no owner. With (c) the claim no longer covers a full parallel run, and the checkpoint epic (#18) must then set a lower keep count | 0.1 |
| 6 | Is producer recompute on a lost artifact worth doing before the soak? | (a) a typed `ARTIFACT_LOST` failure is enough for now and recompute stays the epic's issue 13, (b) build recompute now | (a). A lost artifact needs the store to lose data, which the fault hooks provoke on purpose, and a typed non-retryable failure with a cause is the proof of concept's answer. Recompute needs the workflow to walk the DAG backwards, which is #6's and #18's design | (a). With (b) the workflow epic gains a backwards walk before it exists | none |

### 0.3 New decisions found while reconciling

| # | Question | Options | Recommendation | Default if no answer, and the consequence of the alternative | Changes |
| --- | --- | --- | --- | --- | --- |
| 7 | How does the reference model derive its kinds from the contract (issue 215)? Four copies of the kind list exist: `layout.yaml`, the hand written `reference.schema.json`, the `Literal` lists of `wdf_events.models` and the generated `R__artifact_kinds.sql`. Only the first and the last are generated, and a test compares the first with the schema but nothing compares it with the models [read: `packages/events/tests/test_contract_vectors.py`] | (a) the models build `ContentReference`, `AttemptReference` and the bucket pattern from `layout.yaml` read through package data at import, and `reference.schema.json` is generated from the models with a `--check`, (b) keep the `Literal` lists and add a test that compares them with the layout, (c) generate a `kinds.py` source file with a drift check | (a). It leaves no list to edit, so a kind added to the layout is accepted by the library the moment the table accepts it, and every file that must follow (the schema, the event schemas that embed the reference, the table check) is a generated file whose `--check` fails until regenerated. The hand written narrowings (`ManifestReference`, `CheckpointReference`, `ResultReference`) stay, with a test that each names a kind that exists with the class it assumes | (a). With (b) a new kind is a hand edit in the models and a failing test until it is made, which is safe but is the hand edit that issue 215 reports. With (c) a generated file holds what an import can read | R.1 |
| 8 | How does the reaper know that a content-addressed blob is still referenced by a live job? The index holds rows and cache entries, but no job names a source bundle, an index object, a component or a build-info there. The manifest and a pending patch are on the job row | (a) a link row per job and blob, table `artifact_use (job_id, artifact_row_id)`, written when the workflow commits a reference and when dispatch stores a source, so the reference count is a query over the index, (b) mark and sweep: each run reads every live manifest through `wdf_plan` and derives the live set, (c) a grace period only | (a). The epic names "a reference count from the index table" as its rule and the table has no job column for these kinds. The rule becomes one SQL statement, a paused job holds its inputs for as long as it is not terminal, and a resubmit writes its link before the row | (a). With (b) each run parses a manifest per live batch, cannot see a component or build-info except through a cache entry (the entry of a paused job can expire), and keeps the rule in two places. With (c) the inputs of a long job are deleted, which is the retention bug the epic names as a risk. The cost of (a) is a revisit of the state contract (S.1, state v2) and about three small rows per job | S.1, S.2, C.2, P.2 |
| 9 | Who writes the compile cache entry? The epic's interface row for #11 says the compile task fills it through this library | (a) the workflow, in an activity at the moment it accepts the compile attempt: the worker uploads the wasm and its build-info and returns the references, and the workflow calls `record_cache_entry`, (b) the compile worker, which needs a database login | (a). It keeps workers off the database, keeps "only the workflow decides which attempt's references are recorded" true for the cache as for every other reference, and the first writer still wins in the index | (a). With (b) a login role and a network path from `wdf-workers` to the app database exist for one write, and a zombie compile can fill an entry that the workflow never accepted. The line for #11 in the epic body is amended by this answer, and #6 and #11 confirm it in their own specs | K.3, section 2 |
| 10 | Where does checkpoint metadata (format version, progress marker, wasm hash) live? | (a) a `checkpoint.json` object written after the data object under the same segment, whose reference is the checkpoint reference that events, heartbeat details and the task request carry, and which lists the data objects with their hashes, (b) object user metadata on one object, (c) a header inside the data object | (a). A checkpoint exists only once its metadata object does, so an interrupted write is invisible and the reaper's prune removes it, and it does not depend on what a product does with user metadata. The format of the data stays the checkpoint epic's (#18) | (a). With (b) the metadata is outside the hash and product dependent. With (c) this epic must know the format, which it does not own. The cost of (a) is one more object per checkpoint | K.1 |
| 11 | Who falls back to the previous checkpoint, and who prunes the old ones? | (a) the library verifies one reference and raises `CHECKPOINT_CORRUPT`, the workflow retries the task as a new attempt from the previous checkpoint (the index query gives it), and the reaper, the only role that deletes, prunes to the keep count that #18 sets (at least two), (b) the worker falls back itself, which needs a list of candidates in the task request, (c) workers prune their own, which needs the `delete` action for `simulate` | (a). The task request carries one `resume_checkpoint` and workers hold no database access, so the fallback is a workflow decision. `contracts/store/README.md` says only `reaper` deletes, and a test holds the roles file to it | (a). With (b) the request model changes by a contract-change PR on the envelope. With (c) the roles contract gains a second deleter | K.1, P.1 |

### 0.4 Proposals the lanes pin

These answer the open design items of the epic and fill the gaps that reconciliation found.
Each is a default that the captain may change in the one review, and none needs an answer to start.
Names, shapes and numbers are pinned by lane 0 and the issues, and change afterwards only through a contract-change PR.
After lane 0 merges, `contracts/artifact/README.md` and its files are the source and this section is only a pointer.

| Item | Proposal | Epic item |
| --- | --- | --- |
| Client surface | One class `ArtifactClient(endpoint, credentials, retry, chunk_bytes)` that holds one role's credentials. `put_content(source, kind, name)` for the content-addressed kinds, `put_attempt(source, kind, name, producer, tenant, batch, seed, segment)` for the attempt-scoped kinds, `get(reference, dest)` that returns only after the size and SHA-256 matched, `get_bytes(reference, max_bytes)` for small objects, and `head(reference)`. A source is bytes, a path or a binary stream. The consumer surface has no listing call, which is how the aggregate task never lists a bucket. The reaper has its own lister | detail issue 1 |
| Upload path | A content-addressed key is the hash of the bytes, so a path source is hashed first and a stream is spooled to a scratch file while it is hashed (bounded by the cap of its kind), then sent with `If-None-Match: *` as a cheap extra guard, where an existing object is a success because the write is idempotent. An attempt-scoped key is known up front, so the hash is taken while streaming. Parts of 8 MiB (an estimate) above one part, aborted on any failure so no incomplete upload is left behind. After the upload the size is checked, and where a product returns an MD5 entity tag for a single part it is compared with the MD5 taken while streaming. The test records per product which of these hold, as facts and not as a gate, because the consumer check is the authority | detail issue 5 |
| Index first, then bytes | `put_content` indexes the row (inserting it, or reviving a soft deleted one) and then sends the bytes, so the collector can never delete an object that a revived row names without the client putting it back. A crash between the two leaves a live row whose object a retry restores. The submit role cannot read, so the client never relies on a `HEAD` for a write | detail issue 2 |
| Download path | The object is read into a temporary file beside its destination, the size and SHA-256 are checked while streaming, and the file is renamed only when both match, so no reader sees unverified bytes. A mismatch or a 404 is fetched once more, then `ARTIFACT_LOST` carrying the first cause | detail issue 4 |
| Failure taxonomy | Seven codes of the epic and one more. `STORE_UNAVAILABLE`: backoff within the deadline, then a retryable attempt failure. `STORE_FORBIDDEN` (new): a 403 or a bad credential is a platform fault that is never retried and is a non-retryable failure. `ARTIFACT_NOT_FOUND` and `ARTIFACT_CORRUPT`: one re-fetch, then `ARTIFACT_LOST`. `ARTIFACT_LOST`: non-retryable, classified by #6 and #18. `OUTPUT_UPLOAD_FAILED`: retryable, and a full store (a 500 that survives the retries) maps here. `OUTPUT_MISSING`: non-retryable. `CHECKPOINT_CORRUPT`: not retried in place, the workflow falls back (decision 11), and `ARTIFACT_LOST` when no earlier checkpoint verifies. The surface of each is a typed exception with `code`, a `task.attempt.failed` event whose `reason` starts with `<CODE>: ` (the `Reason` model already takes free text up to 1,024 characters, so no event schema changes), and the input `artifact_failures` counted by code. The class names `retryable` and `non-retryable` are proposals that #6 maps into its failure class table in its lane 0 | detail issue 4 |
| Heartbeats | The client is synchronous and thread safe, takes a `deadline` and a `progress` callback, and never heartbeats itself. The worker (#10) heartbeats from its own thread, so a large transfer cannot starve it, and the transfer time is bounded by the task deadline. A test runs a 256 MiB transfer beside a 100 ms beat and asserts the largest gap | risks |
| Commit by reference | `commit(conn, references, job_id, task, accepted_attempt) -> Committed` is what the workflow's record activity calls under the artifact role. It refuses a reference of another job, task or attempt, indexes the rest, and links content-addressed references to the job (decision 8). A worker only returns references. The zombie test uploads attempts `r0a1` and `r0a2`, commits the second, uploads the first again late, and asserts that the consumer reads the second, that no reference names the first prefix and that the reaper removes it | detail issue 2 |
| Staging contract | `contracts/artifact/staging.yaml` pins, per task type of `contracts/job/registry.json`, the declared outputs: file name, kind and cap. Compile: `component.wasm` (kind `component`) and `build-info.json` (kind `build-info`). Simulate: `results.xml` (kind `result`). Aggregate: `summary.json` (kind `summary`). The scratch directory holds `input/` (read only once staged, with `input.json` written by the worker, whose content is #11's, and each staged input under its reference name) and `output/` (writable). A declared output that is absent or not a regular file is `OUTPUT_MISSING`. An undeclared regular file is not uploaded and is listed in the report and in the log tail, never a failure, because a guest's temporary file must not fail 100,000 attempts. A symbolic link or a directory is ignored and listed. #11 confirms the file names in its own spec, and the registry declares no `output` kind today, so the contract carries an empty list for it | detail issue 3 |
| Output tail | The last 64 KiB of the guest's combined standard output and error is kept in a bounded buffer and uploaded as kind `log-tail`, name `tail.log`, even when the attempt failed. Loki owns the full logs. The decision record with #13 is this row and Closure | detail issue 10 |
| Checkpoint objects | A checkpoint is one data object (more if #18 splits it) and `checkpoint.json`, written last, under `<tenant>/<batch>/<job>/<seed>/<task>/<attempt>/<segment>/`. The metadata holds `format_version` and `progress` (both opaque to this epic and set by #18), `wasm_sha256`, and the reference of each data object. The checkpoint reference is that of `checkpoint.json` and is under 400 bytes, which fits a heartbeat detail. `verify_checkpoint` reads the metadata, then each data object through the hash check, and any fault is `CHECKPOINT_CORRUPT`. Checking that the wasm hash matches the resuming task is #18's | detail issue 6 |
| Aggregate index format | Kind `index`, content-addressed in `wdf-sources`, written as canonical JSON Lines: line 1 is a header (`schema`, `job`, `tenant`, `task`, `partial` as `refuse` or `allow`), then one entry per input sorted by task id, each with the task id, a status of `succeeded`, `failed` or `missing`, and the result reference when the status is `succeeded`. The same inputs in any order give the same bytes and the same hash. A reader streams it, verifies each listed result through the client, and refuses a non-succeeded entry unless the header allows partial results. Every result key of a listed reference must carry the job's tenant as its first segment, which stops a submitter supplied index from reading another tenant's results through the shared `aggregate` credential | detail issue 7 |
| Compile cache | `lookup(conn, client, key)` reads the live entry and checks that the wasm exists with the recorded size (a `HEAD`), and is a miss otherwise. An error of the database or the store is raised and is never a miss, so 100,000 jobs cannot each queue a build. The full hash check happens where the wasm is used, by the consumer, and an `ARTIFACT_LOST` of a cache sourced component makes the workflow call `invalidate_cache_entry` and rebuild, a rule that #6 confirms in its spec. The cache entry also names the build-info of its wasm: the epic body says build-info is stored under the wasm's hash, but a content-addressed key is the hash of the object's own bytes in the artifact contract, so the link is the column `build_info_uri` of the entry (S.1) | detail issue 12 |
| Reaper windows | `contracts/artifact/retention.yaml` holds the windows of question 1, the checkpoint keep count (2), the cache window (14 days) and for content-addressed blobs an unreferenced grace of 2 days and a tombstone interval of 1 hour. The loader refuses a window above the safety net of `contracts/state/retention.yaml` for its bucket, a cache window below twice `MAX_JOB_DURATION_DAYS`, a keep count below two, a failed or partial window shorter than the succeeded one, and a status that is not terminal in `contracts/status/status.yaml` | detail issue 8 |
| Reaper algorithm | One run, idempotent and safe to repeat. (1) For each terminal job past its window, mark its attempt-scoped rows deleted, then delete every object under `<tenant>/<batch>/<job>/` and `<tenant>/<batch>/aggregate/<job>/` in the two attempt-scoped buckets, which removes zombie and orphan prefixes with it, and treat the job as done when a listing is empty. (2) For each non-terminal job, keep the newest checkpoints up to the keep count and delete the rest. (3) Soft delete cache entries unused for the cache window. (4) Collect content-addressed blobs in two phases: tombstone a live row that no live cache entry and no non-terminal job names and that is older than the grace, then, in a later run and under the row lock, delete the object of a row that is still tombstoned past the tombstone interval and record `purged_at`. A revive takes the same row lock, so it either comes first and the delete is skipped or comes after and the client puts the bytes back. A run takes a Postgres advisory lock so two never overlap | detail issue 8 |
| Schedule | A Temporal Schedule `wdf-artifact-reaper` every 15 minutes (an estimate), overlap policy skip, a catch-up window of 1 hour, starting a workflow whose one activity runs a reaper pass with a heartbeat per job and a retry policy (S3b found activities that need one). The task queue and schedule names are constants until the workflow epic pins the Temporal naming. A pod that hosts the worker needs a service image, which does not exist yet, so deployment belongs to the workflow epic's worker deployment | detail issue 8 |
| Store roles and grants | The `aggregate` role reads `wdf-sources`, because the aggregate task opens its index object there and the contract at version 2 grants it only `wdf-results` (R.2). The `wdf_reaper` database role reads the job columns it needs and the use table and sets `deleted_at` and `purged_at`, and `wdf_artifacts` writes the use table, revives and invalidates (S.2). The index builder activity needs no read: it builds from references the workflow recorded and writes with `indexer` | interfaces |
| Metric inputs | New inputs in `contracts/state/inputs.yaml`, all SQL over what exists: `artifact_failures` by code from the `task.attempt.failed` reasons, `checkpoint_age_seconds` (the age of the newest checkpoint row of each non-terminal job, the largest), `reaper_deleted` (rows soft deleted in the last hour), `reaper_overdue` (attempt-scoped rows of terminal jobs past their window and not deleted, which is the reaper's lag), and `cache_hits` and `cache_misses` from `task.skipped` events with reason `compile-cache-hit` and from compile `task.started` events. Bytes per bucket and the capacity alert exist already | detail issues 9 and 10 |
| Fault hooks | `python -m wdf_artifactclient.faults delete\|flip-byte\|truncate\|corrupt-checkpoint --endpoint URL --credentials FILE --role ROLE --uri URI`, and `scripts/artifacts/store-outage.sh stop\|start` that scales the store statefulset. Filling the disk is the existing `disk-full` mode of the contract script, and the hooks name it and do not copy it. #15 calls these and owns the scenarios | detail issue 11 |

## 1. Goal and non-goals

Goal: artifacts move between tasks by reference, and every consumer verifies what it receives.
Tasks, retries and zombie workers can never change an artifact a consumer was told to read.
Checkpoint artifacts for long jobs (#18) use the same mechanism.
Every artifact failure has a defined cause, retry rule and surface, and retention never deletes the inputs of a job that can still run.

### Delivered already, and what is left

The epic body was written before the state epic delivered the index table, the retention windows, the store roles and the contract script.
Everything in the first block below is built on `main` and is cut from this epic.
Names follow the artifact contract: the epic's "wasm" is kind `component`, and its "aggregate index object" is kind `index`.

| Epic body item | State on `main` | Where it is, or what is left |
| --- | --- | --- |
| Reference type with uri, SHA-256, size, name, kind and producer | Delivered, about 300 bytes [ran: the canonical result reference vector is 329 bytes] | `contracts/artifact/reference.schema.json`, `wdf_events.models`. Left: the models cap the kinds as `Literal` lists (issue 215), so R.1 derives them from the layout |
| Addressing rule by kind and the bucket mapping | Delivered | `contracts/artifact/layout.yaml` and its loader `check.sh` (artifact v2), the key checks of `wdf_artifactindex.reference.validate`. Left: functions that build keys, which the client needs (C.1) |
| The four buckets, credentials per role, capacity metrics, lifecycle safety nets | Delivered | `contracts/store/` v2, `deploy/objectstore/`, `scripts/storage/store/`, the `store_bytes` and `store_capacity_share` inputs of `contracts/state/inputs.yaml`. Left: the `aggregate` role cannot read `wdf-sources`, where its index object lives (R.2) |
| Artifact index table and the writer and reaper roles | Delivered | `packages/artifactindex/`, `0300_artifact_index.sql`, `R__roles.sql`. Left: the reaper cannot read the job row, nothing links a job to a content-addressed blob, and a soft deleted row cannot be revived (S.1, S.2) |
| Compile cache rows, first writer wins, a wasm named by a live entry is never soft deleted | Delivered at the index level | `record_cache_entry`, `touch_cache_entry`, `mark_deleted -> Pinned`, the partial unique index and the foreign key. Left: lookup by key (no function exists), the build-info link, invalidation, expiry and the cache client (S.1, K.3, P.1) |
| Retention safety net per bucket | Delivered | `contracts/state/retention.yaml`, `contracts/store/lifecycle.yaml`, the lifecycle job of the store with its 60 minute detection interval, the S3 contract script `wdf_storecheck` (base, access, lifecycle, disk-full, recover and capacity modes). Left: the reaper's own windows (0.2, P.1) |
| The terminal clock | Delivered | `terminal_at` of `accepted_job`, set by the guarded move into a terminal status |
| Capacity guard: bytes per bucket, high-water alert, disk-full test | Delivered | the `store_capacity_share` input alerting above 70 percent, the `disk-full` and `recover` modes and the runbook of `deploy/objectstore/README.md`. Cut from this epic. Left: that a full store surfaces as `OUTPUT_UPLOAD_FAILED` (C.1) |
| Invariant that a result ready event has an artifact row | Delivered | `result-ready-without-artifact`. Left: a retention invariant (S.2) |
| The artifact client library | Left | C.1 |
| Reference model handling | Left | R.1, decision 7 |
| Commit by reference | Left | C.2 |
| Task I/O staging contract and the declared-output rule | Left | 0.1, C.3 |
| Checkpoint artifacts | Left | K.1, decisions 10 and 11 |
| Aggregate index object: format, reader, builder function | Left. The kind and the bucket exist, the format does not | 0.2, K.2 |
| Retention policy and the reaper as a Temporal Schedule, including collection of unreferenced blobs | Left | 0.2, P.1, P.2, P.3 |
| Failure taxonomy, its events, metrics and alerts | Left | 0.1, C.1, S.2 |
| Output tail capture | Left | C.3 |
| Fault hooks for #15 | Left | F.1 |
| Producer recompute (detail issue 13) | Deferred by the epic | question 6 |

Non-goals for this pass:

- everything the epic lists under "Does not own": the store product, its deployment, buckets and credentials, the index table and capacity (#3), message schemas (#4), how a DAG names outputs (#5), the worker process (#10), guest I/O (#11), the checkpoint format, interval, resume entrypoint and progress marker (#18), dashboards (#13), logs, and cross-job deduplication of simulate and aggregate outputs
- the services that call this library, none of which exists yet (the workflow, the worker, the dispatch API), so every caller is tested through a stand-in, and the real use of `commit`, staging and the cache belongs to #6, #10, #11 and #24
- the aggregate index builder activity, which is the workflow's
- deploying the reaper's Temporal worker as a pod, which needs a service image
- producer recompute, and store outage and corruption scenarios beyond the hooks (#15)
- the `Days` lifecycle rule observed on aged objects and the abort of incomplete multipart uploads, which the state epic left to the characterization soak (#16)
- a maximum job duration above 7 days, and every window for it (decision 9)
- a verifier skill, feature files and verify recipes (decision 1)

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with `uv`, Python 3.13, `pytest`, `hypothesis` and `boto3` and `temporalio` from `uv.lock`, the recipes `just test`, `just itest [domain]` and `just ci`, and `scripts/lint.sh` | `flake.lock`, `uv.lock`, recipe table | none, exists [read: `justfile`, `just/*.just`, `uv.lock` holds `boto3` 1.43.123 and `temporalio` 1.35.0] |
| Dev environment | `deploy/versions.yaml` images map with `chrislusf/seaweedfs` 4.51, `rustfs/rustfs` 1.0.4, `timescale/timescaledb` 2.31.0-pg16 and `temporalio/temporal` 1.9.1, all by digest, and the policy in `docs/versions-policy.md`. This epic adds no image | schema v1 | none, exists [read] |
| Cluster | Module contract, preset contract and presets, topology, the cluster lock and recipes: `just up` (which now applies the namespaces), `just down`, `just nuke`, `scripts/tilt/run.sh <preset>` | `contracts/module/`, `contracts/preset/`, `contracts/topology/` (each v1) [ran: each `VERSION` is 1], `scripts/up.sh`, `docs/cluster-host.md` | none, exists |
| Envelope | Events contract: the CloudEvents profile, `task.attempt.failed` with its free text `reason`, `task.checkpoint.committed`, `task.result.ready`, `task.skipped` with reason `compile-cache-hit`, and the key formulas | `contracts/events/` events v3 [ran: `VERSION` is 3] | none, exists |
| Envelope | Event library: `wdf_events.models` (`ContentReference`, `AttemptReference`, `ResultReference`, `CheckpointReference`, `ProducerRef`, `TaskRequestData` with `inputs` and `resume_checkpoint`), `build_event`, `to_event_row` | `packages/events/` library v1 | none, exists [ran: the kinds of `ContentReference` and `AttemptReference` are fixed lists] |
| Envelope | Artifact contract: the reference shape, the four buckets, the ten kinds with their addressing class and key template, the attempt identity pattern, `check.sh` and the vectors | `contracts/artifact/` artifact v2 [ran: `VERSION` is 2] | none, exists. Lane 0 adds files and bumps it to 3 |
| Envelope | Key derivation: `attempt_identity` and the five input `compile_cache_key`. The compat window: `MAX_JOB_DURATION_DAYS` of 7 | `packages/keys/` keys v1, `packages/compat/` compat v1 | none, exists |
| Envelope | Stage contract: the workflow indexes references and emits the result and checkpoint events, and the worker writes only heartbeat details | `contracts/stages/` stages v1 [read: `stages.yaml`] | none, exists |
| Envelope | Instrumentation rules for metric names and labels, which the inputs of S.2 follow without defining metrics | `contracts/instrumentation/` v1 | none, exists |
| Envelope | The T1 container helper `wdf_itest.Session.container(image_key, ports, command, env, mounts, tmpfs)`, the `t1` fixture, the two lock files and the 1 GiB cap | `packages/itest/` | none, exists |
| Validator | The job contract: the registry (outputs per task type, and an aggregate task that accepts `result` inputs or one index object alone, with the codes `index-and-inputs`, `index-job-not-alone` and `index-unresolved`), `params.source` of a compile task, and `wdf_plan.manifest_reference`, `task_request_fields` and `read_plan`. The batch manifest interface: the dispatch API stores the manifest by hash through the artifact client before the row is inserted | `contracts/job/` job v2 [ran: `VERSION` is 2], `packages/plan/` plan v1, `docs/delivery/epics/04-validator.md` | none, exists |
| State | State contract: the `artifact` table and its rules, the writers table, the retention windows, the inputs and the invariant queries | `contracts/state/` state v1 [ran: `VERSION` is 1] | none, exists. S.1 bumps it to 2 |
| State | Libraries: `wdf_artifactindex` (`index_reference`, `mark_deleted`, `references_to`, `record_cache_entry`, `touch_cache_entry`), `wdf_jobstate` (`terminal_at`, the status vocabulary), `wdf_eventstore`, `wdf_stateops` (the group roles of `R__roles.sql`, the inputs, the invariants, the batch bench), `wdf_migrate` with the `appdb` fixture and the `wdf.migrations` entry point | libraries v1 | none, exists [read] |
| Storage | Store contract: nine roles with their actions per bucket, lifecycle rules, products and known gaps. The contract script `wdf_storecheck` and its credentials file format `{"<role>": {"access_key", "secret_key"}}`. The deployed store with generated identities and the runbook | `contracts/store/` store v2 [ran: `VERSION` is 2], `packages/storecheck/`, `deploy/objectstore/`, `scripts/storage/store/identities.sh` | none, exists. R.2 bumps the contract to 3 and exposes the store fixture, which lives only in the tests' `conftest.py` today |
| Spikes | Product facts of S5: path style addressing, bucket level roles, a past dated `Date` rule observed, a full store refuses with a 500 and keeps what it holds, SeaweedFS needs `-volume.max=0`. S8: 100,000 objects of 1 KiB took 371 MiB and a peak working set of 533 MiB only with `GOMEMLIMIT=512MiB` (the shipped settings were killed at 1 GiB after 87,809). S3 and S3b: activities need a retry policy, a 2 MiB payload limit, Temporal's Postgres grows about 35 KiB per job | `docs/delivery/spikes/object-store.md`, `docs/delivery/spikes/S8/state-footprint.md`, `docs/delivery/spikes/temporal.md` | none |
| Spikes | Temporal in T1: the dev server container and the in-process worker pattern | `packages/temporal/tests/` [read] | none, exists |
| Epic bodies of #6, #10, #11, #13, #15, #18 and #24 | What each expects of this epic, as the rules table below records | read 2026-11-03 | none |

No `revisit` child issue is open against the dev environment, cluster, envelope, validator or state epics [ran: `gh-axi issue list --label revisit --state all` shows five, all closed].
The four revisit children that this spec adds are R.1 (envelope), R.2, S.1 and S.2 (state).
The names below were checked free on `main` [ran: no `packages/artifactclient/`, `packages/artifactreaper/`, `scripts/artifacts/`, `contracts/artifact/failures.yaml`, `staging.yaml`, `retention.yaml` or `index.schema.json` exists].

### Produces (each pinned precisely)

Names, shapes and versions below are proposals that lane 0 and the issues pin, and a change after the pin is a contract-change PR with a consumer list and a bump of the contract `VERSION`.
For a generated schema or file that PR carries the contract file, the model under `packages/` and the regenerated output together, because the drift check fails otherwise.
Epic text and notes link to these files and never copy them.

| To | What | Where it lives (path) | Shape (schema file, signature, exit codes, key names) | Version |
| --- | --- | --- | --- | --- |
| #6, #10, #11, #13, #15, #18, #24 | Artifact contract additions: the failure taxonomy, the staging contract with the size caps, the aggregate index format and the reaper windows | `contracts/artifact/` | `failures.yaml`, `staging.yaml`, `retention.yaml` and `index.schema.json`, each with a generated schema, vectors under `vectors/<name>/`, and the sections of `README.md`. `python -m wdf_artifactclient.contract --check FILE` exits 0 valid, 1 with `reason: <code>: <detail>` on stderr, 2 usage. Reasons: `schema-violation`, `unknown-kind`, `unknown-task-type`, `output-not-in-registry`, `duplicate-code`, `window-above-safety-net`, `cache-window-below-twice-max-job-duration`, `keep-below-two`, `failed-shorter-than-succeeded`, `unknown-status`, `entry-without-reference`, `reference-with-failure-status`, `wrong-kind`, `header-not-first`. `python -m wdf_artifactclient.gen [--check]` writes and checks the schemas | artifact v3 |
| #6, #10, #11, #24, #15 | Artifact client library | `packages/artifactclient/` (import `wdf_artifactclient`) | `ArtifactClient`, `put_content`, `put_attempt`, `get`, `get_bytes`, `head`, the key builders `content_key` and `attempt_key`, the typed errors `StoreUnavailable`, `StoreForbidden`, `ArtifactNotFound`, `ArtifactCorrupt`, `ArtifactLost` (with its cause), `OutputUploadFailed`, `OutputMissing` and `CheckpointCorrupt`, each with `code`. `python -m wdf_artifactclient verify --endpoint URL --credentials FILE --role ROLE REF.json` exits 0 verified, 1 with `code: <CODE>` (and `cause: <CODE>` for a lost artifact) on stderr, 2 usage | artifactclient v1 |
| #6 | Commit by reference and the cache client | `wdf_artifactclient.commit`, `wdf_artifactclient.cache` | `commit(conn, references, job_id, task, accepted_attempt) -> Committed`, `lookup(conn, client, key) -> Hit \| Miss`, `record(conn, wasm, build_info, key, versions) -> Won \| Lost \| WasmGone`, `invalidate(conn, key, at)`, `touch(conn, key, at)` | artifactclient v1 |
| #10, #11 | Staging | `wdf_artifactclient.staging` | `stage_inputs(client, inputs, scratch) -> Staged`, `collect_outputs(client, scratch, task_type, producer, ...) -> Collected(references, undeclared)`, `capture_tail(stream, cap)`. The stand-in guest and worker for tests are in `wdf_artifactclient.testing` | artifactclient v1 |
| #18, #6 | Checkpoints | `wdf_artifactclient.checkpoint` | `write_checkpoint(client, source, producer, segment, meta) -> reference`, `verify_checkpoint(client, reference) -> Verified`, `prune_plan(references, keep) -> list`, and the index queries of `wdf_artifactindex` that give the previous checkpoint | artifactclient v1 |
| #6, #5, #24 | Aggregate index | `wdf_artifactclient.aggregate` | `build_index(entries, job, tenant, task, partial) -> bytes`, `put_index(client, data) -> reference`, `read_index(client, reference, tenant) -> iterator of entries`, `open_results(client, entries, scratch) -> iterator`. `python -m wdf_artifactclient.bench --results N [--twice] [--endpoint URL --credentials FILE] [--dsn DSN --jobs N]` exits 0 when the counts are right, 1 when not, and prints `results_put`, `results_verified`, `index_entries`, `index_sha256`, `lists` and the seconds of each phase | artifactclient v1 |
| the cluster, #6 | Reaper and its Schedule | `packages/artifactreaper/` (import `wdf_artifactreaper`) | `reap_once(conn, client, now, windows) -> ReapReport`. `python -m wdf_artifactreaper run-once --dsn DSN --endpoint URL --credentials FILE [--now ISO] [--dry-run]` exits 0, 1 a failed pass, 2 usage, 3 database unreachable, and prints one line per counter (`jobs`, `deleted_objects`, `kept_live`, `pruned_checkpoints`, `expired_cache_entries`, `tombstoned`, `purged`). `schedule ensure\|trigger\|worker --temporal-address ADDR`. The Schedule id and the task queue are constants | artifactreaper v1 |
| #15 | Fault hooks | `wdf_artifactclient.faults`, `scripts/artifacts/store-outage.sh` | as the proposals table. Every hook prints what it changed and exits 0, 1 on failure, 2 usage | artifactclient v1 |
| #4 consumers | Reference models that follow the layout, and a generated reference schema | `packages/events/` (`wdf_events.layout`), `contracts/artifact/reference.schema.json` | `wdf_events.layout()` returns the parsed layout read through package data, `python -m wdf_events.schemagen --check` also covers the reference schema | library v1, no API change |
| #3 consumers, #6 | State contract version 2 and the index additions | `contracts/state/`, `packages/artifactindex/` | table `artifact_use (job_id, artifact_row_id)`, columns `build_info_uri` and `purged_at` of `artifact`, `link_use`, `uses_of`, `lookup_cache_entry`, `invalidate_cache_entry`, `expired_cache_entries`, `unreferenced_content`, `tombstone`, `purge`, `revive` (and `index_reference` that revives a soft deleted row and then returns `Revived`), `checkpoints_of`, `previous_checkpoint`, and the exported schemas `artifact-row/2.json` and `artifact-use/1.json` | state v2, artifactindex v2 |
| #3 consumers, #13, #16, #17 | Retention invariant, inputs and grants | `contracts/state/invariants/`, `contracts/state/inputs.yaml`, `packages/stateops/` | invariant `deleted-artifact-of-live-job`, the inputs of the proposals table, and the grants of `R__roles.sql` for the use table and the reaper | state v2 |
| #3 consumers, the cluster | Store contract version 3 and the reusable fixture | `contracts/store/roles.yaml`, `wdf_storecheck.fixtures` | `aggregate` gains `get` on `wdf-sources`. The plugin offers `running(t1, product, directory, tmpfs)` and the fixtures `store` (module scoped, both products) and `store_session` (one container for a session, SeaweedFS), with `GOMEMLIMIT=512MiB` set for SeaweedFS | store v3 |

### Rules agreed with epics that do not exist yet

These are stated here so the owner epic confirms them in its own spec, and a lane never has to guess.

| Epic | Rule |
| --- | --- |
| #6 Workflow | Calls `commit` for the accepted attempt only. Builds the aggregate index with `build_index` in an activity before the aggregate task. Looks up the cache before placing a compile task and fills it with `record` after accepting a compile attempt (decision 9). Treats `ARTIFACT_LOST` of a cache sourced component as an invalidation and a rebuild. Maps the codes of `failures.yaml` into its failure class table. Chooses the previous checkpoint after `CHECKPOINT_CORRUPT` (decision 11) |
| #10 Workers | Stage inputs, run, collect outputs and write checkpoints through this library with the credentials of the role of the task type, and heartbeat from a separate thread using the `progress` callback |
| #11 Toolchain | Confirms the file names of the staging contract and the schema of `build-info.json`, returns the component and build-info references from a compile task, and writes no database row |
| #24 Dispatch | Stores the manifest and the source bundle through `put_content` with the size cap of the staging contract, which indexes the row first, and links the source to the job before the row is inserted |
| #18 Checkpoint | Sets the format, interval, progress marker and the keep count, which is at least two (the retention loader refuses less). The metadata fields `format_version` and `progress` are opaque to this epic |
| #13 Observability | Turns the new inputs into metrics and alerts |
| #15 Failure scenarios | Calls the fault hooks and owns the scenarios |

## 3. Lanes (disjoint file ownership)

Package names are proposals.
Lane 0 owns every package it creates until the owning lane takes over the glob, as the method says for stubs.

Departures from the method, stated so that the captain approves them knowingly:

- Section 0 carries more than five questions, as its introduction says.
- Four issues edit files of closed epics and are `revisit` children of them: R.1 of the envelope epic (#4, and it is the existing issue 215 re-parented), R.2 of the state epic (#3, the store contract and its fixture), and S.1 and S.2 of the state epic (the index, the grants, the invariants and the inputs).
  Each adds one dated closure addendum line to the spec of its epic, as the method says.
  The three addendum lines of the state epic are appended to one list, and a conflict is resolved by keeping every line.
- Lane Needs run across lanes, because a test over a stub would assert only `NotImplementedError` and the library is accepted by the store and the database that it works against: C.1 waits for R.1 and R.2, C.2 for S.1, K.1 for S.1, K.2 for lane 0, P.1 for S.2, K.1 and C.1, P.2 for C.2 and K.3, F.1 for C.1 and K.1.
- Lane 0 pins the contracts as files with their loader, and the skeletons of two packages with stubs, while the code belongs to lanes C, K, P and F.
  A conformance test in each skeleton accepts `NotImplementedError` only for the names of its `STUBBED` list, and the owning issue empties the list.
- The root `pyproject.toml` is edited once, by lane 0 (issue 0.3), to list the two new workspace members in its dependency group and its sources.
- `docs/delivery/method.md` is not edited by this spec.
  Its verifier table still lists `verify-storage` for #12, and line 112 points the exit test at "spec section 7", which is section 6 here.
  The envelope, validator and state specs record the same follow-up.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| R Revisit | 2 | R.1: `packages/events/**`, `contracts/artifact/reference.schema.json`, one closure addendum line of `docs/delivery/epics/03-envelope.md`. R.2: `contracts/store/**`, `packages/storecheck/**`, `scripts/storage/store/**`, one closure addendum line of `docs/delivery/epics/05-state-storage.md` | R.1, R.2 | nothing (first) |
| S State additions | 1 | S.1: `packages/artifactindex/**`, `contracts/state/{README.md,VERSION,schemas/**}`, `contracts/state/vectors/artifacts/**`, one closure addendum line of `docs/delivery/epics/05-state-storage.md`. S.2: `packages/stateops/**`, `contracts/state/{inputs.yaml,inputs.schema.json,invariants/**}`, `contracts/state/vectors/{inputs,invariants}/**`, one closure addendum line of the same file | S.1, S.2 | S.1 needs R.1 for its last step. S.2 needs S.1 and 0.1 |
| 0 Contracts and skeleton | 1 | `contracts/artifact/{README.md,VERSION,failures.yaml,failures.schema.json,staging.yaml,staging.schema.json,index.schema.json,retention.yaml,retention.schema.json}`, `contracts/artifact/vectors/{failures,staging,index,retention}/**`, `packages/artifactclient/**` and `packages/artifactreaper/**` until the lanes below take over, the root `pyproject.toml` edit and `uv.lock` | 0.1, 0.2, 0.3 | 0.2 needs 0.1, 0.3 needs 0.1 (barrier, merged alone) |
| C Client core | 1 | `packages/artifactclient/src/wdf_artifactclient/{client,errors,keys,commit,staging,__main__}.py`, `packages/artifactclient/tests/test_{client,keys,commit,staging}*.py` | C.1, C.2, C.3 | lane 0, R.1 and R.2 for C.1, C.1 and S.1 for C.2, C.1 for C.3 |
| K Kinds | 1 | `packages/artifactclient/src/wdf_artifactclient/{checkpoint,aggregate,cache,bench}.py`, `packages/artifactclient/tests/test_{checkpoint,aggregate,cache,bench}*.py` | K.1, K.2, K.3 | lane 0, C.1 and S.1 for K.1, C.1 for K.2, C.2 and S.1 for K.3 |
| P Reaper | 1 | `packages/artifactreaper/**` | P.1, P.2, P.3 | lane 0, C.1, K.1 and S.2 for P.1, P.1, C.2 and K.3 for P.2, P.2 for P.3 |
| F Faults | 1 | `packages/artifactclient/src/wdf_artifactclient/faults.py`, `packages/artifactclient/tests/test_faults*.py`, `scripts/artifacts/**` | F.1 | lane 0, C.1 and K.1 |

Shared-file rules:

- `uv.lock` is the one hotspot.
  Lane 0 commits the `uv lock` result for the two new workspace members.
  A dependency lands in the PR that needs it, and none is expected: `boto3`, `temporalio`, `psycopg`, `pydantic`, `pyyaml` and `hypothesis` are locked.
  A lock conflict is never resolved by hand, and `uv lock` is rerun after rebase.
- `packages/artifactclient/src/wdf_artifactclient/__init__.py` and `packages/artifactclient/tests/conftest.py` are lane 0's and are not edited by a lane.
  A lane imports by module path (`wdf_artifactclient.checkpoint`), so no re-export is needed, and a lane keeps its fixtures in its own test files.
- `contracts/artifact/README.md` is lane 0's.
  After lane 0 merges, a file of the pinned contracts changes only through a contract-change PR.
- `contracts/state/README.md` and `contracts/store/README.md` belong to S.1 and R.2, and S.2 adds its own table rows to the first in the PR that adds the files.
- The three addendum lines of `docs/delivery/epics/05-state-storage.md` are appended to the list at its end, one line per PR.
- `deploy/versions.yaml`, `justfile`, `just/*.just`, `docs/dev-setup.md`, `deploy/host/ports.yaml`, `.github/workflows/*` and `deploy/**` are not touched.
  Host access to the store and the database goes through `kubectl port-forward` bound to 127.0.0.1, as the state epic does, so no ports row is added.
- Generated files (`contracts/artifact/*.schema.json`, `contracts/artifact/reference.schema.json`, `contracts/state/schemas/**`, `contracts/state/inputs.schema.json`, `R__artifact_kinds.sql`) are committed, the `gen --check` commands regenerate them, and a lane never hand edits one.
- No tracked file holds a credential.

## 4. Issues

Each block below is a lane issue in the [lane issue template](../../../.github/ISSUE_TEMPLATE/lane-issue.md), and is replaced by `#NNN` links after filing.
Each issue copies its Owns line from the lane table and uses its Test line as the Verifier section.
Done means, for every issue: `just ci` is green on the branch (it runs `just itest`, so every lane's gate needs Docker), the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files of section 3 changed.
Acceptance lines are commands with an observable result.
Commands are written without the `nix develop -c uv run --frozen` prefix that every Python command takes, and T2 commands run under `flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 ...` as the cluster epic says.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 Reference model and artifact client library | R.1 (the model), C.1 (the client) |
| 2 Key layout and commit by reference | C.1 (keys), C.2 (commit and the zombie test), S.1 (the use links). The layout itself is delivered |
| 3 Task I/O staging contract | 0.1 (the contract), C.3 |
| 4 Failure taxonomy | 0.1 (the table), C.1 (the typed errors), S.2 (the count input) |
| 5 Upload path | C.1 |
| 6 Checkpoint artifacts | K.1 |
| 7 Aggregate index object | 0.2 (the format), K.2 |
| 8 Retention policy and reaper | 0.2 (the windows), P.1, P.2, P.3. The per bucket lifecycle safety nets are delivered |
| 9 Capacity guard | delivered by the state epic, with the full store mapping in C.1 |
| 10 Output tail capture and the decision record | C.3, and section 0.4 |
| 11 Fault injection hooks | F.1 |
| 12 Compile result cache | S.1 (index functions), K.3, P.1 (expiry) |
| 13 Producer recompute | deferred (question 6) |
| not in the epic's list: the reference models, the store role, the state additions and the grants | R.1, R.2, S.1, S.2 |

### Consumes and produces per issue

| Issue | Consumes | Produces |
| --- | --- | --- |
| R.1 | artifact v2 layout, `wdf_events.models`, compat v1 | `wdf_events.layout`, the generated `reference.schema.json` |
| R.2 | store v2, `wdf_storecheck`, S8 figures | store v3, `wdf_storecheck.fixtures` |
| S.1 | state v1, artifactindex v1, R.1 | state v2 tables and columns, the index additions |
| S.2 | S.1, state v1 inputs and invariants, failures table of 0.1 | the grants, the invariant, the new inputs |
| 0.1 | artifact v2, job v2 registry, S8 figures | `failures.yaml`, `staging.yaml`, the loader and generator |
| 0.2 | 0.1, status v1, state retention | `index.schema.json`, `retention.yaml` |
| 0.3 | 0.1, the package conventions of earlier epics | the skeletons, the workspace edit |
| C.1 | lane 0, R.1, R.2, `wdf_storecheck.s3` | keys, typed errors, the client, `verify` |
| C.2 | C.1, S.1 | `commit`, the revive protocol of `put_content`, the zombie test |
| C.3 | C.1, staging contract | `stage_inputs`, `collect_outputs`, `capture_tail`, the stand-in guest |
| K.1 | C.1, S.1 | `write_checkpoint`, `verify_checkpoint`, `prune_plan` |
| K.2 | C.1, 0.2 | the index builder and reader, the bench |
| K.3 | C.2, S.1 | `lookup`, `record`, `invalidate`, `touch` |
| P.1 | 0.2, C.1, K.1, S.2 | `reap_once` passes 1 to 3, `run-once` |
| P.2 | P.1, C.2, K.3 | pass 4, the collector |
| P.3 | P.1, P.2, `temporalio`, the dev server image | the Schedule, workflow, activity and `schedule` commands |
| F.1 | C.1, K.1 | `faults`, `store-outage.sh` |

### Issue blocks

#### R.1 Reference models derive their kinds from the layout (revisit of #4, issue 215)

Lane R, tier T0, with the events package tests that `just ci` already runs.
Needs: nothing.
Scope: `wdf_events.models` builds `ContentReference`, `AttemptReference` and the bucket pattern from `layout.yaml` read through package data (decision 7), through one reader `wdf_events.layout()` that `wdf_artifactindex` reuses in S.1.
The narrowings stay and a test holds each to the layout.
`reference.schema.json` is generated from the models.
A test records, with `wdf_compat.breaking_changes`, whether adding a kind widens the event schema `task.requested` compatibly, so the rule for a kind addition (a minor or a contract-change) is executable.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/events -q -rs` passes with no skip, including `test_kinds_come_from_the_layout` (models built from a layout with an added kind validate a reference of it) and `test_narrowings_name_existing_kinds`
- [ ] `uv run --frozen python -m wdf_events.schemagen --check` exits 0 and `git diff --stat contracts/` is empty after a regeneration, so no existing schema byte changed
- [ ] with one enum value removed from `reference.schema.json` in a scratch copy, the same command exits 1 naming `contracts/artifact/reference.schema.json`
- [ ] `uv run --frozen pytest packages/events -q -k wheel` passes, so the built wheel reads the layout from package data

#### R.2 Store contract version 3 and the reusable store fixture (revisit of #3)

Lane R, tier T1, and T2 in the exit test.
Needs: nothing.
Scope: `aggregate` gains `get` on `wdf-sources` in `roles.yaml` (the aggregate task opens its index object there).
The `running` function and the `store` fixture of `packages/storecheck/tests/conftest.py` become the plugin `wdf_storecheck.fixtures`, registered through the `pytest11` entry point as `wdf_itest` and `wdf_migrate` are, with a `store_session` fixture that starts one SeaweedFS for a session.
SeaweedFS in the fixture runs with `GOMEMLIMIT=512MiB`, which S8 found necessary for 100,000 objects within the 1 GiB cap.
Owns: as the lane table.
Acceptance:

- [ ] `contracts/store/check.sh contracts/store/roles.yaml` exits 0 and `VERSION` reads 3
- [ ] `uv run --frozen pytest packages/storecheck -q -rs` passes with no skip on both products, where the access matrix derives its expected outcomes from `roles.yaml`, so the new grant is observed and no expectation is edited by hand
- [ ] `uv run --frozen python -m wdf_storecheck.gen --check` exits 0
- [ ] `uv run --frozen pytest packages/storecheck -q -k plugin_fixture` passes, which resolves `store` and `store_session` from the entry point and not from a `conftest.py`
- [ ] `uv run --frozen pytest packages/storecheck -q -k wheel` passes
- [ ] T2, recorded by the exit test: `scripts/storage/store/identities.sh render` shows the grant for `aggregate` and the deployed store passes the access mode (row 12)

#### S.1 Artifact use links, cache entry columns and the index additions (revisit of #3)

Lane S, tier T1.
Needs: R.1 (for the last step, which makes `wdf_artifactindex` read the layout through `wdf_events.layout()`).
Scope: migration `0301_artifact_use.sql` adds table `artifact_use (job_id uuid, artifact_row_id bigint references artifact (row_id), created_at, primary key (job_id, artifact_row_id))` and the nullable columns `build_info_uri` (a foreign key to `artifact (uri)`, so a live entry pins its build-info as it pins its wasm) and `purged_at` to `artifact`.
Both columns are expand only, so the runner needs no `--allow-contract`.
The functions of section 2 are added: `index_reference` revives a soft deleted row and returns `Revived`, `link_use` is idempotent and refuses a missing or purged row, `lookup_cache_entry`, `invalidate_cache_entry`, `expired_cache_entries`, `tombstone`, `purge`, and `unreferenced_content`, which is the whole live reference rule of decision 8 as one statement: no live cache entry names the blob (as wasm or build-info), no use row of a non-terminal job names it, no non-terminal job row holds it as `manifest_ref` or `pending_patch_ref`, and it is older than the grace.
`checkpoints_of` and `previous_checkpoint` serve decision 11.
The state contract moves to version 2 with the table, the columns and the exported schemas `artifact-row/2.json` and `artifact-use/1.json`, generated by the package's `gen`.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactindex -q -rs` passes with no skip, including `test_unreferenced_content_respects_live_jobs`, `test_paused_job_holds_its_inputs`, `test_cache_entry_pins_wasm_and_build_info`, `test_revive_of_a_tombstoned_row` and `test_from_empty_and_from_previous_release` (the migration applied from the schema of the earlier release and from nothing)
- [ ] `uv run --frozen python -m wdf_artifactindex.gen --check` exits 0, and exits 1 naming `schemas/artifact-row/2.json` when a column is renamed in the model in a scratch copy
- [ ] `uv run --frozen python -m wdf_migrate check --dsn DSN` exits 0 against a database with every package's migrations applied (the test starts one)
- [ ] `uv run --frozen pytest packages/artifactindex -q -k wheel` passes, with the migrations read as package data

#### S.2 Grants, retention invariant and metric inputs (revisit of #3)

Lane S, tiers T1 and T2.
Needs: S.1 and 0.1 (the failure codes).
Scope: `R__roles.sql` gives `wdf_reaper` the read of the job columns it needs (`job_id`, `tenant`, `batch_id`, `status`, `terminal_at`, `manifest_ref`, `pending_patch_ref`), the read of `artifact_use` and the update of `deleted_at` and `purged_at`, and gives `wdf_artifacts` the insert on `artifact_use` and the update of `deleted_at` (for a revive and an invalidation), and the catalog test holds the file to the writers table.
The invariant `deleted-artifact-of-live-job` reports a soft deleted row that a use row of a non-terminal job names, and a soft deleted attempt-scoped row of a non-terminal job that is not a checkpoint.
The inputs of section 0.4 are added to `inputs.yaml`.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/stateops -q -rs` passes with no skip, including the grants test (the reaper stand-in login can read what it needs and cannot update `status` or any other column, and `wdf_artifacts` cannot soft delete outside its columns) and `test_retention_invariant_reports_a_seeded_violation`
- [ ] `uv run --frozen python -m wdf_stateops.inputs --check contracts/state/inputs.yaml` exits 0, and every file under `contracts/state/vectors/inputs/invalid/` exits 1 with a `reason:` line
- [ ] `uv run --frozen pytest packages/stateops -q -k "artifact_failures or cache_hits or reaper_overdue"` passes against seeded events and rows
- [ ] `uv run --frozen python -m wdf_stateops.gen --check` exits 0
- [ ] T2, recorded by the exit test: `python -m wdf_stateops.inputs --dsn DSN` on the cluster prints every new input (row 17)

#### 0.1 Failure taxonomy, staging and limits contracts

Lane 0, tier T0.
Needs: nothing.
Scope: `failures.yaml` (code, cause, retry rule, attempt failure class, surface), `staging.yaml` (per task type: declared outputs with file name, kind and cap, the directory names, the undeclared rule, the log tail cap, the per kind caps of question 5), their generated schemas, vectors, the loader `python -m wdf_artifactclient.contract --check` and `python -m wdf_artifactclient.gen`, and the README sections.
The artifact contract moves to version 3.
The loader checks that the declared outputs of each task type equal those of `contracts/job/registry.json`, so the registry stays the source.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen python -m wdf_artifactclient.contract --check contracts/artifact/failures.yaml` and the same for `staging.yaml` exit 0
- [ ] every file under `contracts/artifact/vectors/failures/invalid/` and `vectors/staging/invalid/` exits 1 with a `reason:` line of the directory name
- [ ] `uv run --frozen python -m wdf_artifactclient.gen --check` exits 0
- [ ] `contracts/artifact/check.sh` still exits 0 for every valid reference vector and 1 for every invalid one, so the version bump broke no consumer
- [ ] `VERSION` reads 3 and the README names the consumers

#### 0.2 Aggregate index format and reaper windows contracts

Lane 0, tier T0.
Needs: 0.1.
Scope: `index.schema.json` (the header line and the entry line), `retention.yaml` with the values of question 1 and the loader rules of section 0.4, vectors for both, and the README sections.
The retention loader reads the safety net of `contracts/state/retention.yaml` and the terminal set of `contracts/status/status.yaml` through package data.
Owns: as the lane table.
Acceptance:

- [ ] `python -m wdf_artifactclient.contract --check contracts/artifact/retention.yaml` exits 0, and each invalid vector under `vectors/retention/invalid/` exits 1 with the reason named by its directory (`window-above-safety-net`, `cache-window-below-twice-max-job-duration`, `keep-below-two`, `failed-shorter-than-succeeded`, `unknown-status`, `schema-violation`)
- [ ] a valid index vector of three entries (succeeded, failed, missing) passes, and each invalid vector under `vectors/index/invalid/` exits 1 (`entry-without-reference`, `reference-with-failure-status`, `wrong-kind`, `header-not-first`)
- [ ] `python -m wdf_artifactclient.gen --check` exits 0

#### 0.3 Client and reaper package skeletons

Lane 0, tiers T0 and T1 (the wheel test).
Needs: 0.1.
Scope: `packages/artifactclient/` and `packages/artifactreaper/` with `pyproject.toml`, the module files of section 3 as stubs that raise `NotImplementedError("not implemented: owned by lane C")` and the same for K, P and F, the `STUBBED` conformance tests, the contract files read as package data, the plugin `wdf_artifactclient.testing` (a stand-in guest and a stand-in worker that run a Python callable against a scratch directory), the root `pyproject.toml` edit and the lock.
Owns: as the lane table.
Acceptance:

- [ ] `uv sync --frozen` and `uv lock --check` exit 0
- [ ] `uv run --frozen pytest packages/artifactclient packages/artifactreaper -q -rs` passes, where every stub is listed in its `STUBBED` and accepted only as `NotImplementedError`
- [ ] `uv run --frozen pytest packages/artifactclient packages/artifactreaper -q -k wheel` passes, so each built wheel loads its contract files and imports from a clean environment

#### C.1 Streaming put and get with typed errors and retries

Lane C, tiers T0 and T1.
Needs: lane 0, R.1, R.2.
Scope: `keys.py` (`content_key` and `attempt_key` built from the layout, refusing what the layout would refuse), `errors.py` (the typed errors of `failures.yaml`), `client.py` (the surface of section 0.4: streaming hash, the spool for a stream source, `If-None-Match`, multipart above one part with abort on any failure, the retry policy of 4 tries with a doubling delay and jitter bounded by the deadline, classification of a store response into a typed error, the `progress` callback), and `verify`.
The retry numbers are estimates and live in one `RetryPolicy`.
A put against a store on a size capped tmpfs maps to `OUTPUT_UPLOAD_FAILED` after the retries and leaves the existing objects readable.
The package README records, per product, whether `If-None-Match` is honoured and whether a single part returns an MD5 entity tag.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactclient -q -rs -k "keys or client"` passes on both products with no skip, including `test_round_trip_at_the_part_boundary` (0 byte, 1 byte, one part, one part plus one byte, 40 MiB), `test_flipped_byte_is_corrupt_then_lost`, `test_deleted_object_is_not_found_then_lost`, `test_wrong_credentials_are_forbidden_and_not_retried`, `test_multipart_abort_leaves_no_parts`, `test_stream_is_read_chunk_by_chunk`, `test_full_store_maps_to_output_upload_failed` and `test_heartbeat_gap_during_a_large_transfer`
- [ ] the hypothesis test `test_distinct_producers_never_share_a_key` passes, and every key the builders produce for every kind passes `wdf_artifactindex.reference.validate`
- [ ] `uv run --frozen python -m wdf_artifactclient verify ...` prints `code: ARTIFACT_LOST` and `cause: ARTIFACT_CORRUPT` and exits 1 for a stored object with a flipped byte, and exits 0 for an intact one
- [ ] the retry policy test with a fake clock shows no retry of a not found, a forbidden or a corrupt, and total sleep never beyond the deadline
- [ ] `uv run --frozen pytest packages/artifactclient -q -k wheel` passes

#### C.2 Commit by reference and the zombie test

Lane C, tier T1.
Needs: C.1, S.1.
Scope: `commit.py` (`commit`), the index-first order and the revive handling of `put_content`, and the zombie and tenant tests.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactclient -q -rs -k commit` passes, including `test_zombie_upload_changes_nothing` (attempts `r0a1` and `r0a2` upload, `r0a2` is committed, `r0a1` uploads again late, the consumer reads the bytes of `r0a2`, and no row names the prefix of `r0a1`), `test_commit_refuses_another_attempt_job_or_task`, `test_content_addressed_commit_links_the_job` and `test_reused_attempt_is_caught_by_the_hash` (an overwrite of a committed key is read as `ARTIFACT_CORRUPT`, which shows why an identity is never reused)
- [ ] `test_revive_before_put_never_leaves_a_live_row_without_an_object` passes with a hook that interleaves a purge between the index step and the put, run for every ordering of the two steps

#### C.3 Task I/O staging and the output tail

Lane C, tier T1.
Needs: C.1.
Scope: `staging.py` as in section 2, from `staging.yaml`, and the stand-in guest tests.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactclient -q -rs -k staging` passes, including `test_declared_outputs_of_every_task_type_match_the_registry`, `test_missing_declared_output_is_output_missing`, `test_undeclared_file_is_not_uploaded_and_is_listed`, `test_symlink_and_directory_are_ignored`, `test_compile_outputs_are_content_addressed_and_simulate_output_is_attempt_scoped`, `test_tail_keeps_the_last_cap_bytes_even_on_failure` and `test_a_thousand_small_outputs`
- [ ] `test_staged_inputs_are_read_only_and_named_by_reference` passes, with `input.json` written by the caller and not by the library
- [ ] the staging test is parametrized by a runner callable, so #10's real runner passes the same test

#### K.1 Checkpoints

Lane K, tier T1.
Needs: C.1, S.1.
Scope: `checkpoint.py` as in section 0.4 (decisions 10 and 11).
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactclient -q -rs -k checkpoint` passes, including `test_write_then_verify`, `test_interrupted_write_is_invisible` (the data object exists and `checkpoint.json` does not), `test_flipped_data_byte_is_checkpoint_corrupt`, `test_missing_metadata_is_checkpoint_corrupt`, `test_prune_plan_keeps_the_newest_and_never_fewer_than_two`, `test_previous_checkpoint_after_corruption` and `test_reference_fits_a_heartbeat_detail` (under 400 bytes, and a `task.checkpoint.committed` event built from it validates)
- [ ] `wasm_sha256` and the opaque fields round trip

#### K.2 Aggregate index object

Lane K, tiers T0 and T1.
Needs: C.1, 0.2.
Scope: `aggregate.py` and `bench.py` as in section 2.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactclient -q -rs -k "aggregate or bench"` passes, including `test_index_is_deterministic_in_any_order`, `test_partial_policy_refuse_and_allow`, `test_foreign_tenant_result_is_refused`, `test_aggregate_never_lists` (a counter on the store client shows zero listing calls while a thousand results are opened), `test_missing_or_corrupt_result_names_its_entry` and `test_hundred_thousand_entries_build_in_bounded_memory` (the builder and the reader hold no list of the entries)
- [ ] `uv run --frozen python -m wdf_artifactclient.bench --results 1000 --twice` exits 0 and prints `results_verified: 1000` and an equal `index_sha256` for both runs, and the full size run is row 8 of the exit test

#### K.3 Compile cache client

Lane K, tier T1.
Needs: C.2, S.1.
Scope: `cache.py` as in section 0.4 (decision 9).
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactclient -q -rs -k cache` passes, including `test_first_writer_wins_between_two_builds`, `test_lookup_miss_when_the_wasm_is_missing_or_the_wrong_size`, `test_lookup_error_is_raised_and_never_a_miss` (the store stopped, then the connection closed), `test_corrupt_cached_wasm_is_lost_and_invalidation_makes_a_miss` and `test_entry_names_wasm_and_build_info`
- [ ] the stand-in workflow test records the compile attempt through `commit` and then `record`, so the order that decision 9 states is the one tested

#### P.1 Reaper core: terminal clock, checkpoint prune, cache expiry

Lane P, tier T1.
Needs: 0.2, C.1, K.1, S.2.
Scope: `reap_once` passes 1 to 3 of the algorithm in section 0.4 and the `run-once` command, with the advisory lock and the `--now` and `--dry-run` options, under the `wdf_reaper` stand-in login and the `reaper` store role.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactreaper -q -rs -k reap` passes, including `test_running_paused_and_admitted_jobs_are_never_touched` (at any age), `test_failed_job_is_kept_longer_than_a_succeeded_one`, `test_window_boundaries_by_status`, `test_zombie_prefix_of_a_terminal_job_is_removed`, `test_summary_under_the_aggregate_prefix_is_removed`, `test_newest_checkpoints_are_kept_for_a_running_job`, `test_pass_after_a_crash_between_mark_and_delete_completes`, `test_two_overlapping_runs_do_not_double_work` and `test_cache_entries_expire_by_last_use`
- [ ] `python -m wdf_artifactreaper run-once --dry-run ...` prints the counters and changes no row or object (the test compares a listing before and after)

#### P.2 Content-addressed collection

Lane P, tier T1.
Needs: P.1, C.2, K.3.
Scope: pass 4 of the algorithm, with the two phases and the injectable hook points that the race tests use.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactreaper -q -rs -k collect` passes, including `test_a_wasm_named_by_a_live_cache_entry_is_never_collected`, `test_the_loser_of_a_cache_race_is_collected_after_the_grace`, `test_a_paused_jobs_manifest_source_and_component_are_kept`, `test_nothing_younger_than_the_grace_is_tombstoned`, `test_reap_races_resubmit` (a resubmit of identical bytes between the tombstone and the purge, in every ordering, ends with either a live row and its object or a purged row and no live link, never a live row without an object) and `test_purge_is_skipped_after_a_revive`
- [ ] after a full run on a store holding every kind, `python -m wdf_stateops.invariants --dsn DSN` exits 0, which is the soak invariant of the epic's risk list

#### P.3 Temporal Schedule

Lane P, tier T1 (Temporal dev server container).
Needs: P.1, P.2.
Scope: the workflow, the activity and `schedule ensure|trigger|worker`, as in section 0.4.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactreaper -q -rs -k schedule` passes against the dev server of `temporalio/temporal` and an in-process worker, including `test_ensure_twice_leaves_one_schedule`, `test_trigger_runs_one_pass_and_returns_its_report`, `test_overlapping_trigger_is_skipped`, `test_activity_failure_is_retried` and `test_paused_schedule_runs_nothing`
- [ ] the package README states that the worker is run by hand against the cluster and that a pod needs a service image

#### F.1 Fault hooks

Lane F, tier T1, and T2 in the exit test.
Needs: C.1, K.1.
Scope: `faults.py` and `scripts/artifacts/store-outage.sh`, and a table driven test that reads `failures.yaml` and fails when a code has no scenario that provokes it.
Owns: as the lane table.
Acceptance:

- [ ] `uv run --frozen pytest packages/artifactclient -q -rs -k faults` passes, including one scenario per code: a flipped byte gives `ARTIFACT_CORRUPT` then `ARTIFACT_LOST`, a deleted object gives `ARTIFACT_NOT_FOUND` then `ARTIFACT_LOST`, a stopped store container gives `STORE_UNAVAILABLE` within the deadline, a corrupted checkpoint gives `CHECKPOINT_CORRUPT` and the previous one verifies, a full store gives `OUTPUT_UPLOAD_FAILED`, a missing declared output gives `OUTPUT_MISSING`, wrong credentials give `STORE_FORBIDDEN`, and `test_every_code_of_the_taxonomy_has_a_scenario` passes
- [ ] `shellcheck scripts/artifacts/store-outage.sh` is clean (it runs in `just lint`)
- [ ] T2, recorded by the exit test: `store-outage.sh stop` then `verify` gives `STORE_UNAVAILABLE`, and `start` then `verify` gives exit 0 (row 15)

## 5. Order, stubs and spikes

- First, in parallel: R.1, R.2, S.1 (its last step waits for R.1) and 0.1.
  Then 0.2, 0.3 and S.2 in parallel.
  Lane 0 (0.1, 0.2, 0.3) is the barrier and is merged alone, with an estimate of three days.
- After lane 0, R.1 and R.2: C.1 starts alone, because every other code lane needs the client core.
  Then C.2, C.3, K.1 and K.2 start together, K.3 follows C.2, and P.1 follows K.1 and S.2.
  P.2 follows P.1, C.2 and K.3, P.3 follows P.2, and F.1 follows C.1 and K.1.
  Then the exit test.
- DAG: ((R.1 ‖ R.2 ‖ S.1 ‖ 0.1), then (0.2 ‖ 0.3 ‖ S.2)), then lane 0 merged, then C.1, then (C.2 ‖ C.3 ‖ K.1 ‖ K.2 ‖ F.1 after K.1), then (K.3 after C.2 ‖ P.1 after K.1 and S.2), then P.2, then P.3, then the exit test.
- Every issue needs only T0 and T1.
  Cluster using work is the T2 half of R.2, S.2 and F.1 and rows 10 to 17 of the exit test, which take `flock /tmp/wdf-cluster.lock`, and T1 work waits for one of the two container slots of `wdf_itest`.
- Questions 1, 2 and 5 are answered before 0.2 merges, questions 3, 4 and 6 need no answer to start, decisions 7 and 8 before R.1 and S.1 start, decision 9 before K.3 starts, and decisions 10 and 11 before K.1 starts, or the defaults apply.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| The module functions of the two skeletons that 0.3 creates | `NotImplementedError("not implemented: owned by lane C")` and the same for K, P and F | each package's `test_stubs.py` accepts that error only for the names in its `STUBBED` list and the real behaviour for the rest, and the owning issue empties its names |
| The stand-in guest and worker of `wdf_artifactclient.testing` | a Python callable that reads `input/` and writes `output/`, standing for the guest of #11 and the runner of #10 | the staging tests are parametrized by the runner, and #10 and #11 add their real runner to the same parameters |
| The stand-in record activity | a test function that calls `commit`, `record` and `link_use` for a hand chosen accepted attempt, standing for the workflow of #6 | #6 runs the zombie and cache tests with its real activity |
| The reaper's Schedule constants (id and task queue) | names that the workflow epic has not pinned | the workflow epic replaces them and a test compares them with its naming contract |
| Stand-in login users for the group roles | a login created by the test and added to `wdf_reaper` or `wdf_artifacts` | the service epics create the real logins and receive the grants tested here |

Spikes: none.
The object counts and heap bound are S8's, and the product facts are S5's.
Three facts are measured by lanes and recorded, not gated: whether each product honours `If-None-Match` and an MD5 entity tag for a single part (C.1), the put and read rates of the client for 100,000 small results (K.2 and row 8), and the largest heartbeat gap during a 256 MiB transfer (C.1).

## 6. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main` taken outside any git repository, with Docker running and the cluster free.
Each command runs from the export root as written, its real output is pasted in the PR that records the run, and it is summarised in Closure.
Rows 10 to 17 take the cluster lock for their whole sequence, for example `flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 bash -c '<commands>'`, because `just up`, `just down` and `just nuke` take the lock themselves unless that variable is set.
The steps that change a file do so in the scratch export and restore it afterwards.
The export is of `main` after every lane has merged, in the order of section 5.
`DSN` stands for the database that a step names and `<port>` for the loopback port of a `kubectl port-forward` that the step opens.
Command prefixes `nix develop -c` are omitted below and every row takes them, and every Python command takes `uv run --frozen`.

| # | Command | Expected |
| --- | --- | --- |
| 1 | `uv sync --frozen` then `uv lock --check` | exit 0 for both, lock unchanged |
| 2 | `for m in wdf_events.schemagen wdf_artifactindex.gen wdf_artifactclient.gen wdf_stateops.gen wdf_storecheck.gen; do python -m $m --check \|\| echo "DRIFT $m"; done` | no output |
| 3 | add to `contracts/artifact/layout.yaml` a kind `report` in `wdf-results` with the attempt-scoped template of `result`, run `python -m wdf_events.schemagen --check` and `python -m wdf_artifactindex.gen --check`, run `python -c "from wdf_artifactindex.reference import validate; ..."` on a `report` reference, then regenerate both, run both checks again and restore the file | the first two commands exit 1 naming `contracts/artifact/reference.schema.json` and `R__artifact_kinds.sql`, the library accepts the `report` reference before the regeneration (it follows the layout), and both checks exit 0 after it, so the library and the table never disagree |
| 4 | `for f in failures staging retention; do python -m wdf_artifactclient.contract --check contracts/artifact/$f.yaml; done`, the same check over every file under `contracts/artifact/vectors/{failures,staging,retention,index}/invalid/`, `contracts/artifact/check.sh` over every vector of `contracts/artifact/vectors/{valid,invalid}`, and `contracts/store/check.sh contracts/store/roles.yaml` | exit 0 for the three valid files, the valid vectors and the roles file, and exit 1 with a `reason:` line for every invalid file |
| 5 | `pytest packages/artifactclient packages/artifactreaper packages/events packages/artifactindex packages/stateops packages/storecheck -q -rs` | all pass with no `SKIPPED` line, on both object store products |
| 6 | `pytest packages/artifactclient packages/artifactreaper packages/artifactindex -v -k "zombie_upload_changes_nothing or flipped_byte_is_corrupt_then_lost or multipart_abort_leaves_no_parts or first_writer_wins_between_two_builds or running_paused_and_admitted_jobs_are_never_touched or reap_races_resubmit or interrupted_write_is_invisible or index_is_deterministic or foreign_tenant_result_is_refused or aggregate_never_lists or ensure_twice_leaves_one_schedule or every_code_of_the_taxonomy_has_a_scenario or heartbeat_gap"` | each named test is listed `PASSED` |
| 7 | `pytest packages/artifactclient packages/artifactreaper packages/events packages/artifactindex packages/stateops packages/storecheck -q -rs -k wheel` | all pass, none skipped, so every package works from a built wheel |
| 8 | `python -m wdf_artifactclient.bench --results 100000 --twice` (starts its own SeaweedFS container with the heap bound when no endpoint is given) | exit 0, `results_put: 100000`, `results_verified: 100000`, `index_entries: 100000` and `lists: 0` for both runs and the same `index_sha256`, with the phase times printed for the record and no timing guard |
| 9 | `scripts/tilt/run.sh ci` inside the locked sequence after `just up` | exit 0 with `appdb`, `temporaldb` and `objectstore` healthy and `objectstore-bootstrap` and `appdb-migrate` completed |
| 10 | `python -m wdf_storecheck run --endpoint http://127.0.0.1:<port> --credentials <state file> --mode base`, then `--mode access` against the deployed store through a port-forward | exit 0 for each, with no `FAIL` line, and with the access matrix showing `aggregate` allowed to get from `wdf-sources` |
| 11 | `python -m wdf_artifactclient.bench --results 2000 --endpoint http://127.0.0.1:<port> --credentials <state file> --dsn DSN --jobs 2` | exit 0, `results_verified: 2000`, and two jobs seeded, one advanced to `succeeded` and one left `running`, each with committed results and a checkpoint |
| 12 | `python -m wdf_artifactreaper run-once --dsn DSN --endpoint http://127.0.0.1:<port> --credentials <state file> --now <timestamp 30 days ahead>`, then the same command again | exit 0, `deleted_objects` above 0 and `jobs: 1` for the succeeded job, `kept_live` for the running one, then `deleted_objects: 0` on the repeat |
| 13 | `python -m wdf_artifactclient verify ... REF.json` on a result reference of the running job, then of the succeeded job | exit 0 for the running job, and exit 1 with `code: ARTIFACT_LOST` and `cause: ARTIFACT_NOT_FOUND` for the reaped one |
| 14 | `python -m wdf_artifactclient.faults flip-byte --role simulate --uri <result of the running job>`, then `verify` on its reference | exit 1 with `code: ARTIFACT_LOST` and `cause: ARTIFACT_CORRUPT` |
| 15 | `scripts/artifacts/store-outage.sh stop`, then `verify` on the reference of the running job, then `scripts/artifacts/store-outage.sh start` and `verify` again | the first `verify` exits 1 with `code: STORE_UNAVAILABLE` in under 60 s, and the second exits 0 once the store is back |
| 16 | `python -m wdf_stateops.invariants --dsn DSN` | exit 0, so no job that can still run lost an artifact to the reaper, and the two workflow comparisons print `skipped:` lines |
| 17 | `python -m wdf_stateops.inputs --dsn DSN --store-endpoint http://127.0.0.1:<port> --credentials <state file>` | a value for every input of `inputs.yaml`, including `artifact_failures`, `checkpoint_age_seconds`, `reaper_deleted`, `reaper_overdue`, `cache_hits` and `cache_misses` |
| 18 | `just doctor`, then `just down` and `just nuke` twice | exit 0 for each, and `just nuke` leaves nothing and repeats cleanly |
| 19 | `just ci` | exit 0 |
| 20 | the `ci` and `state-modules` workflows on `main` | one green run of each, the URLs recorded in Closure |

Not provable by this epic: the real staging by a worker and a guest (#10, #11), the zombie test with Temporal's own fencing and a real workflow recording references (#6), heartbeats under a real Temporal activity, the reaper as a deployed pod and its Schedule on the cluster's Temporal (the workflow epic, which needs a service image), the `Days` lifecycle rule on aged objects and the abort of incomplete multipart uploads (#16), a store outage or a corruption drill beyond the hooks and a restart while full (#15), checkpoints of a multi-day job and their real format (#18), producer recompute, retention behaviour over a multi-day soak, a mitigation of the cache's existence side channel, prefix level restriction of a role, transfer behaviour over a real network, and a maximum job duration above 7 days.

## 7. Parallelism and cost

- Maximum parallel workers: 4 for the first phase (R.1, R.2, S.1 and 0.1), then 3 (0.2, 0.3 and S.2), then 1 for C.1, then up to 5 (C.2, C.3, K.1, K.2 and F.1), planned at 4 because the host runs two T1 containers at a time, then 2 for K.3 and P.1, then 1 each for P.2 and P.3, then 1 for the exit test.
- Host constraints: T0 work needs only `uv` and Python from the dev shell.
  T1 work starts SeaweedFS, RustFS, TimescaleDB and one Temporal dev server by `docker run` on 127.0.0.1, each capped at 1 GiB, with at most two containers across the host.
  T2 work needs the `ci` cluster, and a worker never starts `dev` or `soak`.
- CI cost, stated as estimates: no new workflow and no new image (decision 15), so the added cost is T1 containers in every lane's `just ci`.
  The plan keeps it bounded.
  The client core tests run on both products in two module scoped containers, and every other module of the new packages shares the one session scoped SeaweedFS of `store_session` and isolates itself by tenant name, so the store starts about five times (the two products, the session store, the collector's own store because collection acts on the whole bucket, and the fault module's store, which it stops).
  The database fixture adds a TimescaleDB container per module that needs one, as the state epic's tests already do, and the schedule module adds one Temporal dev server and an image pull of an estimated few hundred MiB on a clean runner.
  The 100,000 result bench runs only in the exit test, and `just ci` runs the 1,000 result form.
  No test waits for a timer: the reaper takes `now` as an argument and the Schedule test triggers it.
- Estimates, not measurements, in worker-days: R.1 (1), R.2 (1.5), S.1 (3), S.2 (2), lane 0 (3), C.1 (4), C.2 (2), C.3 (2.5), K.1 (2.5), K.2 (2.5), K.3 (1.5), P.1 (3), P.2 (3), P.3 (2), F.1 (1.5), exit test (1.5).
  That is about 36 worker-days of effort and about 17 days elapsed along lane 0, C.1, C.2 or K.1, P.1, P.2, P.3 and the exit test, with the cluster lock, the two container slots and `uv.lock` rebases as the main sources of slip.

## 8. Closure (filled at close)

Filled when the epic closes: the commit and the run of the exit test, the workflow runs on `main`, the measured rates of the client for 100,000 small results, the per product facts of C.1, what the architecture note now shows, and what moved to which epic.

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/delivery/method.md](../method.md#revisiting-a-closed-epic)).
