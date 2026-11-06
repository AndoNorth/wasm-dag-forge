# Epic #12: Artifact passing - breakdown

Status: approved
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/12
Architecture note: docs/architecture/13-state-envelope-artifacts.drawio
Depends on (closed epics): Dev environment (#1), Fixed-resource cluster (#2), Job event envelope (#4), DAG configuration schema and validator (#5), Durable state and artifact storage (#3)
Verifier: none, per recorded decision 1.

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-11-03) while preparing this spec, `[read]` means read and not re-run.
The epic issue body was read on 2026-11-03, and this spec was written against that version.
`main` was at `9c7e1c4`, the merge that closed the state and storage epic.
Method: [docs/delivery/method.md](../method.md).
Models: [docs/delivery/epics/05-state-storage.md](05-state-storage.md) (the most recent, fully delivered breakdown, whose structure this spec follows), [docs/delivery/epics/04-validator.md](04-validator.md), [docs/delivery/epics/03-envelope.md](03-envelope.md) and [docs/delivery/epics/02-cluster.md](02-cluster.md).
Spike results consulted: [object store](../spikes/object-store.md) (S5) for the product facts, [state footprint](../spikes/S8/state-footprint.md) (S8) for the 100,000 object figures and the heap bound, and [Temporal](../spikes/temporal.md) (S3, S3b) for the retry-policy finding.
The 2 MiB Temporal payload limit comes from the epic text [read] and not from a spike, as the [envelope spec](03-envelope.md) records.
Tiers, as the earlier breakdowns use them: T0 is host Python with no runtime, T1 is containers started through `wdf_itest` (at most two containers across the host, and a session holds one at a time until R.2 lets it hold a second, named one), and T2 is the cluster from `just up` on preset `ci` under `/tmp/wdf-cluster.lock`.
A test that needs a container carries the `integration` marker and so runs in `just itest` and in every lane's `just ci`, except a test that is also marked `isolated`, which the `wdf_itest` plugin deselects unless the marker expression names `isolated`.
A T2 check is a command and never a pytest test, so `just ci` never needs a cluster.

## 0. Decisions for the captain

The captain answered the five questions of the draft as recommended on 2026-11-03.
The answers are recorded decisions 16 to 20 in 0.1, and 0.2 notes that no question is open.
That is not approval of the spec, which comes with the merge of its pull request.
Decision 18 amends two lines of the epic issue and states the amendment.
Section 0.3 lists the defaults that the lanes build on, with options and recommendations, which the answers left standing, and 0.4 holds the proposals that lane 0 and the issues pin.
A change to a default or a proposal changes only the issues named in its last column.

### 0.1 Recorded decisions

Everything in the epic body stands except where merged work has overtaken it, which section 1 lists, and where decision 18 amends it.
Decisions 1 to 15 were made for earlier epics or by the brief that asked for this spec, and decisions 16 to 20 are the captain's answers to the questions of the draft.

| # | Decision | Source | Applied in |
| --- | --- | --- | --- |
| 1 | No verifier skill, no feature files and no verify recipes for this epic, and the `verify-storage` row of the method (`docs/delivery/method.md` lists it for #3 and #12) is dropped for #12 as it was for #3. The product tests stay: the schema drift checks, the contract loader vectors, the zombie, corruption and fallback tests, the retention safety tests, the race tests of the collector and the aggregate bench. Verifier delta: none | the captain's standing rule, applied in the envelope, validator and state epics | sections 3, 4 and 6 |
| 2 | Python models are the source of anything with a schema, and the exported JSON Schemas are checked in with a drift test. `contracts/artifact/reference.schema.json` is hand written today and becomes generated from the models (R.1). The YAML contracts of lane 0 are validated by a loader whose models generate their schemas | the envelope epic, decision 5, carried | 0.3, R.1, 0.1, 0.2 |
| 3 | `just` stays the entrypoint with logic in scripts. This epic adds no recipe and no `just/*.just` module: tests run through `just test`, `just itest` and `just ci`, and cluster helpers are scripts under `scripts/artifacts/` | the dev environment epic, carried | sections 3 and 4 |
| 4 | Epics are local-development and declarative: no cloud service, no operator and no controller. The reaper is a Temporal Schedule that starts a workflow, and no cron job or controller exists beside it | the brief | P.3 |
| 5 | Runtime libraries read their contract files through package data (`importlib.resources`) and never by repository path, as `wdf_events` does with its `contract/` links. Every new package ships what it reads, and a wheel test imports and uses the built wheel from a clean virtual environment | state epic, decision 11, carried | 0.3, R.1, every package issue |
| 6 | General docs do not hard-code counts. The architecture note and `docs/dev-setup.md` name no number of kinds, codes, roles or lanes, and the counts in this spec belong to the spec | the brief | section 8 (Closure) |
| 7 | Epics 1 to 5 are merged and closed, so this spec consumes their real contracts, libraries and versions as pinned in section 2 | the brief | section 2 |
| 8 | The attempt number is the workflow's own per-task counter, never reused. The client never infers, counts or defaults an attempt: the producer of a reference and every segment of an attempt-scoped key come from the caller, and the library only checks them against the layout | state epic, decision 6, and the events contract | C.1, C.2 |
| 9 | The maximum job duration stays 7 days, and every window of this epic is derived from it by the floors that the loaders enforce. Scaling beyond 7 days is not work here | state epic, decision 7 | 0.2, P.1 |
| 10 | One content-addressed key space is shared across tenants, because the artifact contract has no tenant in `sha256/<hash2>/<hash>`. The existence side channel that follows belongs to this epic (decision 17) | state epic, decision 13 | S.1, K.3 |
| 11 | Compile cache entries are rows of the one artifact index table and there is no second table. The table, the rule that a live entry pins its wasm and first writer wins are delivered (section 1) and are not rebuilt here, with one correction: invalidation and expiry give the soft deleted entry a retired cache key, so a soft deleted entry does not own its key forever (S.1) | state epic, decision 18 | S.1, K.3 |
| 12 | The aggregate index is built by an activity of the workflow just before the aggregate task starts, from the result references that the workflow recorded, and an aggregate-only job takes an index object from its submitter (the epic body, stands). This epic delivers the format, the builder function and the reader, and the activity itself is the workflow epic's (#6) | the epic body | 0.2, K.2 |
| 13 | Guest I/O for checkpoints is a WASI directory, as the checkpoint epic (#18) defines it. This epic stores, verifies and prunes the checkpoint objects, and the input and output directories of a task are the staging contract | the epic body | 0.1, C.3, K.1 |
| 14 | The golden `job.accepted` vector class (issue 216) is a validator vocabulary matter, deferred to the dispatch API epic (issue 24) as an early lane there, and is not taken here | the brief | none |
| 15 | No new CI workflow. The `state-modules` workflow already deploys the store and runs the contract script. Its path filter lists `contracts/store/**`, `contracts/state/**`, `packages/storecheck/**` and `packages/migrate/**`, which cover R.2 and the contract part of S.1 and S.2, but not the packages `packages/artifactindex/**` and `packages/stateops/**` that S.1 and S.2 own. A pull request that changes only those packages does not start the workflow, and `just ci` covers them. The T2 evidence of this epic comes from the exit test | review of `.github/workflows/state-modules.yml` [read] | section 7 |
| 16 | Reaper windows: per status, counted from `terminal_at`. Succeeded keeps results 7 days and checkpoints 1, partial and failed 14 and 3, cancelled and terminated 3 and 1, and a paused job has no reaper rule and keeps its objects until the safety net of `contracts/state/retention.yaml` (results 28 days, checkpoints 14 days). Rejected: one window of 14 days for every terminal status, which keeps a succeeded job's results as long as a failed one's and doubles the disk for no stated reason, and a paused job that expires by the reaper's own rule, which puts a second expiry on jobs that the checkpoint epic owns. A pause beyond the net is outside the proof of concept (`contracts/state/README.md` says so) | captain review, 2026-11-03 | 0.2, P.1 |
| 17 | The compile cache is global and shared across tenants, with expiry by last use after 14 days (twice the maximum job duration, a floor that the loader enforces so the entry of a running job never expires) and no size cap. The existence side channel is accepted and written down: a tenant whose compile is skipped learns that identical source was built before. Rejected: a per tenant cache, which adds a sixth input to `wdf_keys.compile_cache_key` by a contract-change PR with consumers #6 and #11 and builds identical sources once per tenant, and a size cap with least recently used eviction, which adds an eviction rule that nothing needs yet while the capacity alert already covers the bucket | captain review, 2026-11-03 | S.1, K.3, P.1 |
| 18 | The workflow records the compile cache entry, and the build-info is linked by a stored column. The record activity calls `record_cache_entry` after the commit has indexed the wasm and the build-info, and the column `build_info_uri` of the entry names the build-info. Rejected: the compile worker writing the entry, which needs a login role and a network path from `wdf-workers` to the app database for one write and lets a zombie compile fill an entry that the workflow never accepted, and a new addressing class that derives the build-info key from the wasm hash, which changes the layout, the reference schema and the table check for a link that one column gives. Amendment of the epic issue, stated here because the delivered layout made it necessary: the Interfaces row for #11 ("The compile task produces the content-addressed wasm and `build-info.json`, and fills the cache entry through this library") reads, as amended, "The compile task produces the content-addressed wasm and `build-info.json` and returns their references, and the workflow fills the cache entry through this library after the commit", and the Key decision ("Its `build-info.json` is stored under the same hash") reads, as amended, "Its `build-info.json` is stored under its own content hash, because a content-addressed key is the hash of the object's own bytes, and the cache entry names it in a column". This spec is the source where the issue text differs, and #6 and #11 confirm the amendment in their own specs | captain review, 2026-11-03 | S.1, K.3, section 2 |
| 19 | A worker uploads content-addressed bytes only, and the workflow's commit activity indexes the row after the object exists. The commit takes the row lock, checks the object with a `HEAD`, revives a tombstoned or purged row, links the job, and fails the attempt retryably when the object is gone. An object that has no live row (a rejected or zombie compile that never got one, or bytes uploaded again after the row was purged) is removed by the orphan rule of the reaper after a grace period. The index first protocol stays for callers that hold a connection (dispatch). Rejected: the worker indexing first through a database login, which gives workers database access for one more write, and the workflow indexing at the first read, which leaves a cached component without a row, invisible to the reaper as referenced. The content-addressed buckets have no age rule in `contracts/store/lifecycle.yaml`, so without the orphan rule a rejected attempt would leave its objects forever | captain review, 2026-11-03 | C.1, C.2, P.2, S.1 |
| 20 | The reaper's reference count is a link table, `artifact_use (job_id, artifact_row_id)`, written by a named caller for each input class (the table below this one), so the reaper's rule is one query over the index and a paused job holds its inputs for as long as it is not terminal. Rejected: mark and sweep over every live manifest through `wdf_plan`, which parses a manifest per live batch, cannot see a component or build-info except through a cache entry that can expire for a paused job, and keeps the rule in two places, and a grace period only, which deletes the inputs of a long job. The cost is a revisit of the state contract (S.1, state v2) and about three small rows per job | captain review, 2026-11-03 | S.1, S.2, C.2, K.3, P.2 |

Who calls `link_use`, per input class (decision 20):

| Input class | Caller | When |
| --- | --- | --- |
| Manifest and source bundle | the dispatch API (#24), through `put_content` with its connection and `job_id` | before the job row is inserted, and again on a resubmit of identical bytes |
| Patched manifest of a resume | the dispatch API (#24), through `put_content` with its connection and `job_id` | after the job row exists and before `pending_patch_ref` is set, and the job row's `pending_patch_ref` protects it from then on |
| Submitter supplied aggregate index object | the dispatch API (#24), through `put_content` with its connection and `job_id` | before the job row is inserted |
| Workflow built aggregate index object | the workflow's index activity, through `commit` of the reference that `put_index` returns | before the aggregate task is dispatched |
| Component and build-info of an accepted compile attempt | the workflow's record activity, through `commit` | when the compile attempt is accepted |
| Component and build-info taken from a cache hit | the workflow, through `cache.use` with the hit | when it places the task that reads them, so no compile attempt exists to commit them |
| Results and checkpoints | not linked: they are attempt-scoped and belong to the job by key prefix | none |

Results that a submitter supplied index of an aggregate-only job lists are deleted by their producing job's own window, with no check that another live job lists them.
That is ordinary retention of the producing job, and it is a non-goal here.

### 0.2 Questions for the captain

The captain answered all five questions of the draft as recommended, on 2026-11-03.
They are recorded as decisions 16 to 20 in 0.1, with the chosen option and the rejected alternative, and no question is open.
The defaults of 0.3 and the proposals of 0.4 stand until the spec is approved.

### 0.3 Defaults the lanes build on

These carry the remaining open questions of the epic and the design choices found while reconciling it, each with options and a recommendation.
The captain's answers left them standing as the defaults, and they are numbered D1 to D8 in this spec.

| # | Topic | Options | Recommendation | Default if no answer, and the consequence of the alternative | Changes |
| --- | --- | --- | --- | --- | --- |
| D1 | Do tiny results (a digest per seed) need one object each, or may they sit in a Temporal payload or a Postgres row? | (a) one object per result, never inlined, (b) inline below a threshold in a row or a payload | (a). S8 stored 100,000 objects of 1 KiB in 371 MiB at 383 puts per second through 16 threads, with the store heap bounded and a working set peak of 533 MiB, so the count is carried. One code path is the epic's own key decision. The exit test measures a 100,000 result aggregate through the real client | (a). With (b) a second read path, a second retention path and a reference that is sometimes bytes exist, which reverses the key decision "values are never inlined" | K.2 |
| D2 | Should a result object carry an index of its block ids and sizes so a consumer can skip blocks? | (a) the whole object is always read (the epic's proposal), (b) a header index | (a). Block level checks belong to the consumer that parses the blocks, and one hash covers one object | (a). With (b) the object has a second format and a header that the hash does not separate from the data | none |
| D3 | What are the maximum single artifact size, checkpoint size and per-job storage cap? | (a) per object caps by kind in the staging contract (manifest 1 MiB, source bundle 64 MiB, index 128 MiB, component 64 MiB, build-info 1 MiB, result 64 MiB, output 256 MiB, summary 16 MiB, log tail 64 KiB, checkpoint 64 MiB per object) and no per-job cap, (b) as (a) with a per-job byte cap enforced by accounting, (c) larger checkpoint objects | (a). The checkpoint cap is chosen against the store claim of 10,240 MiB: the `ci` profile allows 32 parallel tasks and two kept checkpoints each, so 32 x 2 x 64 MiB is 4,096 MiB. The numbers are estimates and a change is a contract-change PR. The index cap covers 100,000 entries of about 380 bytes (38 MB) with room for three times that | (a). With (b) every attempt-scoped write needs accounting across attempts, which has no owner. With (c) the claim no longer covers a full parallel run, and the checkpoint epic (#18) must then set a lower keep count | 0.1 |
| D4 | Is producer recompute on a lost artifact worth doing before the soak? | (a) a typed `ARTIFACT_LOST` failure is enough for now and recompute stays the epic's issue 13, (b) build recompute now | (a). A lost artifact needs the store to lose data, which the fault hooks provoke on purpose, and a typed non-retryable failure with a cause is the proof of concept's answer. Recompute needs the workflow to walk the DAG backwards, which is #6's and #18's design | (a). With (b) the workflow epic gains a backwards walk before it exists | none |
| D5 | How does the reference model derive its kinds from the contract (issue 215)? Four copies of the kind list exist: `layout.yaml` (the hand written source, validated by `layout.schema.json`), the hand written `reference.schema.json`, the `Literal` lists of `wdf_events.models`, and `R__artifact_kinds.sql`, which is the only generated one. A test compares the layout with the schema but nothing compares it with the models [read: `packages/events/tests/test_contract_vectors.py`] | (a) the models build `ContentReference`, `AttemptReference` and the bucket pattern from `layout.yaml` read through package data at import, and `reference.schema.json` is generated from the models with a `--check`, (b) keep the `Literal` lists and add a test that compares them with the layout, (c) generate a `kinds.py` source file with a drift check | (a). It leaves no list to edit, so a kind added to the layout is accepted by the library the moment the table accepts it, and every file that must follow (the schema, the event schemas that embed the reference, the table check) is a generated file whose `--check` fails until regenerated. The hand written narrowings (`ManifestReference`, `CheckpointReference`, `ResultReference`) stay, with a test that each names a kind that exists with the class it assumes | (a). With (b) a new kind is a hand edit in the models and a failing test until it is made, which is safe but is the hand edit that issue 215 reports. With (c) a generated file holds what an import can read | R.1 |
| D6 | Where does checkpoint metadata (format version, progress marker, wasm hash) live? | (a) a `checkpoint.json` object written after the data object under the same segment, whose reference is the checkpoint reference that events, heartbeat details and the task request carry, and which lists the data objects with their hashes, (b) object user metadata on one object, (c) a header inside the data object | (a). A checkpoint exists only once its metadata object does, so an interrupted write is invisible and the reaper's prune removes it, and it does not depend on what a product does with user metadata. The format of the data stays the checkpoint epic's (#18) | (a). With (b) the metadata is outside the hash and product dependent. With (c) this epic must know the format, which it does not own. The cost of (a) is one more object per checkpoint | K.1 |
| D7 | Who falls back to the previous checkpoint, and who prunes the old ones? | (a) the library verifies one reference and raises `CHECKPOINT_CORRUPT`, the workflow retries the task as a new attempt from the previous checkpoint (the index query gives it), and the reaper, the only role that deletes, prunes a running job to the keep count that #18 sets (at least two) and never touches a paused or admitted job, (b) the worker falls back itself, which needs a list of candidates in the task request, (c) workers prune their own, which needs the `delete` action for `simulate` | (a). The task request carries one `resume_checkpoint` and workers hold no database access, so the fallback is a workflow decision. `contracts/store/README.md` says only `reaper` deletes, and a test holds the roles file to it. The surplus that a paused job carries is bounded by the checkpoint writer (#18), which writes one checkpoint per interval, and the reaper prunes it when the job runs again | (a). With (b) the request model changes by a contract-change PR on the envelope. With (c) the roles contract gains a second deleter | K.1, P.1 |
| D8 | How does a failed compile attempt upload its log tail? The layout puts kind `log-tail` in `wdf-results`, and `compile` has only `wdf-sources: [get, list]` and `wdf-components: [put, multipart]` in `contracts/store/roles.yaml` | (a) grant `compile` `put` and `multipart` on `wdf-results` (store v3, R.2), (b) put the compile tail in `wdf-components` by a new kind, (c) no tail for compile, Loki only | (a). It is one grant in the contract version that R.2 already bumps and it keeps the layout unchanged. The split is at bucket level (`contracts/store/roles.yaml` says prefixes are not tested), so `compile` could in principle write any results key, and the client's key checks and the index are the guard. The tail matters most on a failed compile | (a). With (b) the layout and the schemas change for one kind. With (c) the failed compile has no stored tail, which is the case where it is needed | R.2, C.3 |

### 0.4 Proposals the lanes pin

These answer the open design items of the epic and fill the gaps that reconciliation found.
The captain's answers left them standing, and a change to one is a contract-change PR after lane 0 pins it.
Names, shapes and numbers are pinned by lane 0 and the issues, and change afterwards only through a contract-change PR.
After lane 0 merges, `contracts/artifact/README.md` and its files are the source and this section is only a pointer.

| Item | Proposal | Epic item |
| --- | --- | --- |
| Client surface | One class `ArtifactClient(endpoint, credentials, retry, chunk_bytes)` that holds one role's credentials. `put_content(source, kind, name, *, conn=None, job_id=None)` for the content-addressed kinds, where `job_id` is required when `conn` is given, `put_attempt(source, kind, name, producer, tenant, batch, seed, segment)` for the attempt-scoped kinds, `get(reference, dest)` that returns only after the size and SHA-256 matched, `get_bytes(reference, max_bytes)` for small objects, and `head(reference)`. Every call takes the keyword arguments `deadline` and `progress`. A source is bytes, a path or a binary stream. The consumer surface has no listing call, which is how the aggregate task never lists a bucket. The reaper has its own lister | detail issue 1 |
| Upload path | A content-addressed key is the hash of the bytes, so a path source is hashed first and a stream is spooled to a scratch file while it is hashed (bounded by the cap of its kind), then sent with `If-None-Match: *` as a cheap extra guard, where an existing object is a success because the write is idempotent. An attempt-scoped key is known up front, so the hash is taken while streaming. Parts of 8 MiB (an estimate) above one part, aborted on any failure so no incomplete upload is left behind. After the upload the size is checked, and where a product returns an MD5 entity tag for a single part it is compared with the MD5 taken while streaming. The test records per product which of these hold, as facts and not as a gate, because the consumer check is the authority | detail issue 5 |
| Indexing protocol | Two callers, two orders (decision 19). A caller that holds a connection (dispatch, under the artifact role `wdf_artifacts`) passes `conn` and the keyword `job_id` to `put_content`, which indexes the row first (inserting it, or reviving a soft deleted or purged one), then sends the bytes, then writes the link of the job with `link_use`, so a revived row is never left without its object: a crash between the steps leaves a live row whose object a retry restores, and the link follows the bytes. A caller without a connection (a worker) gets bytes only and returns the reference, and the workflow's `commit` indexes after the object exists. The submit role cannot read, so the client never relies on a `HEAD` for a write | detail issue 2 |
| Download path | The object is read into a temporary file beside its destination, the size and SHA-256 are checked while streaming, and the file is renamed only when both match, so no reader sees unverified bytes. A mismatch or a 404 is fetched once more, then `ARTIFACT_LOST` carrying the first cause | detail issue 4 |
| Failure taxonomy | Seven codes of the epic and one more. `STORE_UNAVAILABLE`: backoff within the deadline, then a retryable attempt failure, and every store fault that is not a missing object, a hash mismatch or a refused credential, including during a checkpoint verification. `STORE_FORBIDDEN` (new): a 403 or a bad credential is a platform fault that is never retried and is a non-retryable failure. `ARTIFACT_NOT_FOUND` and `ARTIFACT_CORRUPT`: one re-fetch, then `ARTIFACT_LOST`. `ARTIFACT_LOST`: non-retryable, classified by #6 and #18. `OUTPUT_UPLOAD_FAILED`: retryable, and a full store (a 500 that survives the retries) and an output that is gone from the store at commit map here. `OUTPUT_MISSING`: non-retryable. `CHECKPOINT_CORRUPT`: only a hash or size mismatch or a missing object of a checkpoint, never a store fault, not retried in place, the workflow falls back (D7), and `ARTIFACT_LOST` when no earlier checkpoint verifies. The surface of each is a typed exception with `code`, a `task.attempt.failed` event whose `reason` starts with `<CODE>: ` (the `Reason` model already takes free text up to 1,024 characters, so no event schema changes), and the input `artifact_failures` counted by code. The class names `retryable` and `non-retryable` are proposals that #6 maps into its failure class table in its lane 0 | detail issue 4 |
| Heartbeats | The client is synchronous and thread safe, takes a `deadline` and a `progress` callback, and never heartbeats itself. The worker (#10) heartbeats from its own thread, so a large transfer cannot starve it, and the transfer time is bounded by the task deadline. A test runs a 256 MiB transfer beside a 100 ms beat and asserts the largest gap | risks |
| Commit by reference | `commit(conn, client, references, job_id, task, accepted_attempt=None)` is what the workflow's record activity calls under the artifact database role, with a client of the `workflow` store role. An attempt-scoped reference is refused unless it names this job, task and accepted attempt. A content-addressed reference takes the row lock (inserting the row if it is absent), checks the object with a `HEAD`, and only then makes the row live: it clears a tombstone or a purge mark and links the job. When the object is gone the commit raises `OutputUploadFailed` and changes nothing, and the workflow retries the attempt, which uploads the bytes again. A worker only returns references. The zombie test uploads attempts `r0a1` and `r0a2`, commits the second, uploads the first again late, and asserts that the consumer reads the second, that no reference names the first prefix and that the reaper removes it | detail issue 2 |
| Staging contract | `contracts/artifact/staging.yaml` pins, per task type of `contracts/job/registry.json`, the declared outputs: file name, kind and cap. Compile: `component.wasm` (kind `component`) and `build-info.json` (kind `build-info`). Simulate: `results.xml` (kind `result`). Aggregate: `summary.json` (kind `summary`). The scratch directory holds `input/` (read only once staged, with `input.json` written by the worker, whose content is #11's, and each staged input under its reference name) and `output/` (writable). A declared output that is absent or not a regular file is `OUTPUT_MISSING`. An undeclared regular file is not uploaded and is listed in the report and in the log tail, never a failure, because a guest's temporary file must not fail 100,000 attempts. A symbolic link or a directory is ignored and listed. #11 confirms the file names in its own spec, and the registry declares no `output` kind today, so the contract carries an empty list for it | detail issue 3 |
| Output tail | The last 64 KiB of the guest's combined standard output and error is kept in a bounded buffer and uploaded as kind `log-tail`, name `tail.log`, even when the attempt failed, with the role of the task type (D8 gives `compile` the put it needs). Loki owns the full logs. The decision record with #13 is this row and Closure | detail issue 10 |
| Checkpoint objects | A checkpoint is one data object (more if #18 splits it) and `checkpoint.json`, written last, under `<tenant>/<batch>/<job>/<seed>/<task>/<attempt>/<segment>/`. The metadata holds `format_version` and `progress` (both opaque to this epic and set by #18), `wasm_sha256`, and the reference of each data object. The checkpoint reference is that of `checkpoint.json` and is under 400 bytes, which fits a heartbeat detail. `verify_checkpoint` reads the metadata, then each data object through the hash check. A hash or size mismatch or a missing object is `CHECKPOINT_CORRUPT`, and a store fault is `STORE_UNAVAILABLE`, which the client retries within the deadline. Checking that the wasm hash matches the resuming task is #18's | detail issue 6 |
| Aggregate index format | Kind `index`, content-addressed in `wdf-sources`, written as canonical JSON Lines: line 1 is a header (`schema`, `job`, `tenant`, `task`, `partial` as `refuse` or `allow`), then one entry per input in task id order, each with the task id, a status of `succeeded`, `failed` or `missing`, and the result reference when the status is `succeeded`. `build_index` takes the entries as an iterator that is already sorted by task id, writes to a stream and fails with `out-of-order` on an unsorted or repeated task id, so it holds no list of the entries. The workflow's index activity reads the recorded results ordered by task id. The same set of entries in order gives the same bytes and the same hash. A reader streams it, verifies each listed result through the client, and refuses a non-succeeded entry unless the header allows partial results. Every result key of a listed reference must carry the job's tenant as its first segment, which stops a submitter supplied index from reading another tenant's results through the shared `aggregate` credential | detail issue 7 |
| Compile cache | The unique index `artifact_cache_key` stays as it is. Invalidation and expiry soft delete the entry and replace its `cache_key` with a retired key (the lowercase hex SHA-256 over the bytes of the old key, a colon and the decimal row id, so the string `old_key:row_id` is hashed and the result is 64 hex characters, which passes the table's shape check and the model pattern), which keeps the row valid under the table's checks (a cache entry needs a 64 hex key) and frees the original key, so a key can be recorded again after an invalidation or an expiry. The row keeps its `wasm_uri` and `build_info_uri`. `lookup(conn, client, key)` reads the live entry and checks that the wasm exists with the recorded size (a `HEAD`), and is a miss otherwise. An error of the database or the store is raised and is never a miss, so 100,000 jobs cannot each queue a build. A hit is linked to the job by `cache.use(conn, hit, job_id)`, which links the wasm and the build-info. The full hash check happens where the wasm is used, by the consumer, and an `ARTIFACT_LOST` of a cache sourced component makes the workflow call `invalidate_cache_entry` and rebuild, a rule that #6 confirms in its spec. The cache entry names the build-info of its wasm by the column `build_info_uri` (S.1, decision 18), `mark_deleted` refuses a soft delete of either object while a live entry names it, and `record_cache_entry` writes the column | detail issue 12 |
| Reaper windows | `contracts/artifact/retention.yaml` holds the windows of decision 16, the checkpoint keep count (2), the cache window (14 days) and for content-addressed blobs an unreferenced grace of 2 days, a tombstone interval of 1 hour and an orphan grace of 2 days. The loader refuses a window above the safety net of `contracts/state/retention.yaml` for its bucket, a cache window below twice `MAX_JOB_DURATION_DAYS`, a keep count below two, a failed or partial window shorter than the succeeded one, and a status that is not terminal in `contracts/status/status.yaml` | detail issue 8 |
| Reaper progress | Two tables written only by the reaper role (S.2, migration in `packages/stateops`): `reaper_job (job_id, results_done_at, checkpoints_done_at)` and `reaper_cursor (name, position, updated_at)`. A done marker is set when a pass finds the listing of a job's prefix empty after deleting, and the cursor holds the continuation of the orphan sweep. They make a pass bounded and restartable | detail issue 8 |
| Reaper algorithm | One run, idempotent and safe to repeat, in six passes. (1) Results: select terminal jobs whose `terminal_at` is between the bucket's hard expiry (the safety net, beyond which the lifecycle rule has removed the objects) and the results window, and that have no `results_done_at`. Mark their rows in `wdf-results` deleted, delete every object under `<tenant>/<batch>/<job>/` and `<tenant>/<batch>/aggregate/<job>/`, which removes zombie and orphan prefixes with it, and set the marker when a listing is empty. (2) Checkpoints of terminal jobs: the same with the checkpoint window, the checkpoint bucket and `checkpoints_done_at`. (3) Prune: for each job in a running status, keep the newest checkpoints up to the keep count and delete the rest. A paused or admitted job is never touched by any pass. (4) Soft delete cache entries unused for the cache window. (5) Collect content-addressed blobs in two phases: tombstone a live row that no live cache entry and no live use row names (the whole rule of `unreferenced_content`) and that is older than the grace, then, in a later run, take the row lock, evaluate `unreferenced_content` again, and only if the row is still tombstoned past the tombstone interval and still unreferenced delete the object and record `purged_at`. `link_use`, `commit` and a connection holder's `index_reference` take the same row lock and revive a tombstoned row, so a reference that arrives after the tombstone is never lost, and a reference to a row already purged is refused or re-put. (6) Orphan sweep: list `wdf-sources` and `wdf-components` in bounded pages from the cursor. An object is an orphan when it has no live row, that is no row at all, a tombstoned row, or a purged row whose object exists again, and it is older than the orphan grace by its last modified time. For an object with a row the sweep takes the row lock and runs the same `unreferenced_content` re-check as `purge`, treats a tombstoned row as an orphan only once it has been tombstoned for the tombstone interval (so pass 5's interval is not bypassed in the same run), and only then deletes the object and records `purged_at`, which serializes it with a revive and with a link. An object with no row is deleted without a lock, and the window that leaves (a row going live between the check and the delete, which needs an old object re-uploaded and committed at that moment) ends in a typed `ARTIFACT_LOST` that the cache rule already handles. A heartbeat is sent after each bounded batch of objects (500, an estimate), never per job, so a job with 100,000 results does not outlast a heartbeat timeout. Every comparison of time, including the tombstone and the purge, uses the `now` argument and never the database clock, so a repeated run with the same `now` finds nothing more to do. A run takes a Postgres advisory lock so two never overlap | detail issue 8 |
| Schedule | A Temporal Schedule `wdf-artifact-reaper` every 15 minutes (an estimate), overlap policy skip, a catch-up window of 1 hour, starting a workflow whose one activity runs a reaper pass with a heartbeat per batch and a retry policy (S3b found activities that need one). The task queue and schedule names are constants until the workflow epic pins the Temporal naming. A pod that hosts the worker needs a service image, which does not exist yet, so deployment belongs to the workflow epic's worker deployment | detail issue 8 |
| Store roles and grants | `aggregate` reads `wdf-sources`, because the aggregate task opens its index object there and the contract at version 2 grants it only `wdf-results`. `compile` puts to `wdf-results` for its log tail (D8). Both are in R.2. The record activity checks content-addressed objects through a client of the `workflow` role, which already reads the sources and components buckets, and the index builder activity writes the index object with `indexer`. The `wdf_reaper` database role reads the job columns it needs and the use table, sets `deleted_at` and `purged_at`, and writes the two progress tables. `wdf_artifacts` writes the use table including the renewal of `last_linked_at`, `build_info_uri`, the revive and the retired key, and is the role of the dispatch API's artifact connection (S.2) | interfaces |
| Metric inputs | New inputs in `contracts/state/inputs.yaml`: `artifact_failures` by code from the `task.attempt.failed` reasons, which needs a labelled form of the sql source (a query that returns a label and a number per row, as `bytes_per_bucket` already yields labelled values) and S.2 extends the input model, the schema and the loader for it. The others are plain SQL over what exists: `checkpoint_age_seconds` (the age of the newest checkpoint row of each non-terminal job, the largest), `reaper_deleted` (rows soft deleted in the last hour), `reaper_overdue` (attempt-scoped rows of terminal jobs past their window, within the hard expiry, and not marked done, which is the reaper's lag), and `cache_hits` and `cache_misses` from `task.skipped` events with reason `compile-cache-hit` and from compile `task.started` events. Bytes per bucket and the capacity alert exist already | detail issues 9 and 10 |
| Fault hooks | `python -m wdf_artifactclient.faults delete\|flip-byte\|corrupt-checkpoint --endpoint URL --credentials FILE --role ROLE --uri URI`, and `scripts/artifacts/store-outage.sh stop\|start` that scales the store statefulset. Filling the disk is the existing `disk-full` mode of the contract script, and the hooks name it and do not copy it. #15 calls these and owns the scenarios | detail issue 11 |

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
| The four buckets, credentials per role, capacity metrics, lifecycle safety nets | Delivered | `contracts/store/` v2, `deploy/objectstore/`, `scripts/storage/store/`, the `store_bytes` and `store_capacity_share` inputs of `contracts/state/inputs.yaml`. Left: the `aggregate` role cannot read `wdf-sources`, where its index object lives, and `compile` cannot put its log tail (R.2) |
| Artifact index table and the writer and reaper roles | Delivered | `packages/artifactindex/`, `0300_artifact_index.sql`, `R__roles.sql`. Left: the reaper cannot read the job row, nothing links a job to a content-addressed blob, and a soft deleted row cannot be revived (S.1, S.2) |
| Compile cache rows, first writer wins, a wasm named by a live entry is never soft deleted | Delivered at the index level | `record_cache_entry`, `touch_cache_entry`, `mark_deleted -> Pinned`, the partial unique index and the foreign key. Left: a soft deleted entry still owns its key, lookup by key (no function exists), the build-info link, invalidation, expiry and the cache client (S.1, K.3, P.1) |
| Retention safety net per bucket | Delivered | `contracts/state/retention.yaml`, `contracts/store/lifecycle.yaml`, the lifecycle job of the store with its 60 minute detection interval, the S3 contract script `wdf_storecheck` (base, access, lifecycle, disk-full, recover and capacity modes). Left: the reaper's own windows (0.2, P.1) |
| The terminal clock | Delivered | `terminal_at` of `accepted_job`, set by the guarded move into a terminal status |
| Capacity guard: bytes per bucket, high-water alert, disk-full test | Delivered | the `store_capacity_share` input alerting above 70 percent, the `disk-full` and `recover` modes and the runbook of `deploy/objectstore/README.md`. Cut from this epic. Left: that a full store surfaces as `OUTPUT_UPLOAD_FAILED` (C.1) |
| Invariant that a result ready event has an artifact row | Delivered | `result-ready-without-artifact`. Left: a retention invariant (S.2) |
| The artifact client library | Left | C.1 |
| Reference model handling | Left | R.1, D5 |
| Commit by reference | Left | C.2 |
| Task I/O staging contract and the declared-output rule | Left | 0.1, C.3 |
| Checkpoint artifacts | Left | K.1, D6 and D7 |
| Aggregate index object: format, reader, builder function | Left. The kind and the bucket exist, the format does not | 0.2, K.2 |
| Retention policy and the reaper as a Temporal Schedule, including collection of unreferenced blobs | Left | 0.2, P.1, P.2, P.3 |
| Failure taxonomy, its events, metrics and alerts | Left | 0.1, C.1, S.2 |
| Output tail capture | Left | C.3 |
| Fault hooks for #15 | Left | F.1 |
| Producer recompute (detail issue 13) | Deferred by the epic | D4 |

Non-goals for this pass:

- everything the epic lists under "Does not own": the store product, its deployment, buckets and credentials, the index table and capacity (#3), message schemas (#4), how a DAG names outputs (#5), the worker process (#10), guest I/O (#11), the checkpoint format, interval, resume entrypoint and progress marker (#18), dashboards (#13), logs, and cross-job deduplication of simulate and aggregate outputs
- the services that call this library, none of which exists yet (the workflow, the worker, the dispatch API), so every caller is tested through a stand-in, and the real use of `commit`, staging and the cache belongs to #6, #10, #11 and #24
- the aggregate index builder activity, which is the workflow's
- deploying the reaper's Temporal worker as a pod, which needs a service image
- producer recompute, and store outage and corruption scenarios beyond the hooks (#15)
- the `Days` lifecycle rule observed on aged objects and the abort of incomplete multipart uploads by the store's lifecycle job, which the state epic left to the characterization soak (#16); the client's own abort of an upload that fails is in scope (C.1)
- a maximum job duration above 7 days, and every window for it (recorded decision 9)
- a verifier skill, feature files and verify recipes (recorded decision 1)
- the golden `job.accepted` vector class (issue 216), which is deferred to the dispatch API epic (issue 24), where it is recorded as an early lane, and is not taken here

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
| Envelope | Stage contract: the workflow emits the result and checkpoint events, and the worker writes references into heartbeat details only. That the workflow also indexes references is `contracts/state/README.md`, which assigns "Index a committed reference and a cache entry" to the artifact client under role `wdf_artifacts` | `contracts/stages/` stages v1 [read: `stages.yaml`, `contracts/state/README.md`] | none, exists |
| Envelope | Instrumentation rules for metric names and labels, which the inputs of S.2 follow without defining metrics | `contracts/instrumentation/` v1 | none, exists |
| Envelope | The T1 container helper `wdf_itest.Session.container(image_key, ports, command, env, mounts, tmpfs)`, the `t1` fixture, the two lock files and the 1 GiB cap. A session holds one container at a time today (`ContainerError: one container at a time`), which R.2 changes | `packages/itest/` | none, exists |
| Validator | The job contract: the registry (outputs per task type, and an aggregate task that accepts `result` inputs or one index object alone, with the codes `index-and-inputs`, `index-job-not-alone` and `index-unresolved`), `params.source` of a compile task, and `wdf_plan.manifest_reference`, `task_request_fields` and `read_plan`. The batch manifest interface: the dispatch API stores the manifest by hash through the artifact client before the row is inserted | `contracts/job/` job v2 [ran: `VERSION` is 2], `packages/plan/` plan v1, `docs/delivery/epics/04-validator.md` | none, exists |
| State | State contract: the `artifact` table and its rules, the writers table, the retention windows, the inputs and the invariant queries | `contracts/state/` state v1 [ran: `VERSION` is 1] | none, exists. S.1 bumps it to 2 |
| State | Libraries: `wdf_artifactindex` (`index_reference`, `mark_deleted`, `references_to`, `record_cache_entry`, `touch_cache_entry`), `wdf_jobstate` (`terminal_at`, the status vocabulary), `wdf_eventstore`, `wdf_stateops` (the group roles of `R__roles.sql`, the inputs, the invariants, the batch bench), `wdf_migrate` with the `appdb` fixture and the `wdf.migrations` entry point | libraries v1 | none, exists [read] |
| Storage | Store contract: nine roles with their actions per bucket, lifecycle rules, products and known gaps. The contract script `wdf_storecheck` and its credentials file format `{"<role>": {"access_key", "secret_key"}}`. The deployed store with generated identities and the runbook | `contracts/store/` store v2 [ran: `VERSION` is 2], `packages/storecheck/`, `deploy/objectstore/`, `scripts/storage/store/identities.sh` | none, exists. R.2 bumps the contract to 3 and exposes the store fixture, which lives only in the tests' `conftest.py` today |
| Spikes | Product facts of S5: path style addressing, bucket level roles, a past dated `Date` rule observed, a full store refuses with a 500 and keeps what it holds, SeaweedFS needs `-volume.max=0`. S8: 100,000 objects of 1 KiB took 371 MiB and a peak working set of 533 MiB only with `GOMEMLIMIT=512MiB` (the shipped settings were killed at 1 GiB after 87,809). S3 and S3b: activities need a retry policy, and Temporal's Postgres grows about 35 KiB per job | `docs/delivery/spikes/object-store.md`, `docs/delivery/spikes/S8/state-footprint.md`, `docs/delivery/spikes/temporal.md` | none |
| Spikes | Temporal in T1: the dev server container and the in-process worker pattern | `packages/temporal/tests/` [read] | none, exists |
| Epic bodies of #6, #10, #11, #13, #15, #18 and #24 | What each expects of this epic, as the rules table below records | read 2026-11-03 | none |

No `revisit` child issue is open against the dev environment, cluster, envelope, validator or state epics [ran: `gh-axi issue list --label revisit --state all` shows five, all closed].
The revisit children that this spec adds are R.1 (envelope), R.2 (state, and envelope for `packages/itest`), S.1 and S.2 (state).
The names below were checked free on `main` [ran: no `packages/artifactclient/`, `packages/artifactreaper/`, `scripts/artifacts/`, `contracts/artifact/failures.yaml`, `staging.yaml`, `retention.yaml` or `index.schema.json` exists].

### Produces (each pinned precisely)

Names, shapes and versions below are proposals that lane 0 and the issues pin, and a change after the pin is a contract-change PR with a consumer list and a bump of the contract `VERSION`.
For a generated schema or file that PR carries the contract file, the model under `packages/` and the regenerated output together, because the drift check fails otherwise.
Epic text and notes link to these files and never copy them.

| To | What | Where it lives (path) | Shape (schema file, signature, exit codes, key names) | Version |
| --- | --- | --- | --- | --- |
| #6, #10, #11, #13, #15, #18, #24 | Artifact contract additions: the failure taxonomy, the staging contract with the size caps, the aggregate index format and the reaper windows | `contracts/artifact/` | `failures.yaml`, `staging.yaml`, `retention.yaml` and `index.schema.json`, each with a generated schema, vectors under `vectors/<name>/`, and the sections of `README.md`. `python -m wdf_artifactclient.contract --check FILE` exits 0 valid, 1 with `reason: <code>: <detail>` on stderr, 2 usage. Reasons: `schema-violation`, `unknown-kind`, `unknown-task-type`, `output-not-in-registry`, `duplicate-code`, `window-above-safety-net`, `cache-window-below-twice-max-job-duration`, `keep-below-two`, `failed-shorter-than-succeeded`, `unknown-status`, `entry-without-reference`, `reference-with-failure-status`, `wrong-kind`, `header-not-first`. `python -m wdf_artifactclient.gen [--check]` writes and checks the schemas | artifact v3 |
| #6, #10, #11, #24, #15 | Artifact client library | `packages/artifactclient/` (import `wdf_artifactclient`) | `ArtifactClient`, `put_content(source, kind, name, *, conn=None, job_id=None, deadline=None, progress=None)` (`job_id` required with `conn`), `put_attempt(source, kind, name, producer, tenant, batch, seed, segment, deadline=None, progress=None)`, `get`, `get_bytes`, `head`, the key builders `content_key` and `attempt_key`, the typed errors `StoreUnavailable`, `StoreForbidden`, `ArtifactNotFound`, `ArtifactCorrupt`, `ArtifactLost` (with its cause), `OutputUploadFailed`, `OutputMissing` and `CheckpointCorrupt`, each with `code`. `python -m wdf_artifactclient verify --endpoint URL --credentials FILE --role ROLE REF.json` exits 0 verified, 1 with `code: <CODE>` (and `cause: <CODE>` for a lost artifact) on stderr, 2 usage | artifactclient v1 |
| #6 | Commit by reference and the cache client | `wdf_artifactclient.commit`, `wdf_artifactclient.cache` | `commit(conn, client, references, job_id, task, accepted_attempt=None) -> Committed`, `lookup(conn, client, key) -> Hit \| Miss`, `use(conn, hit, job_id)`, `record(conn, wasm, build_info, key, versions) -> Won \| Lost \| WasmGone`, `invalidate(conn, key, at)`, `touch(conn, key, at)` | artifactclient v1 |
| #10, #11 | Staging | `wdf_artifactclient.staging` | `stage_inputs(client, inputs, scratch, deadline=None, progress=None) -> Staged`, `collect_outputs(client, scratch, task_type, producer, tenant, batch, seed, deadline=None, progress=None) -> Collected(references, undeclared)`, `capture_tail(stream, cap)`. The stand-in guest and worker for tests are in `wdf_artifactclient.testing` | artifactclient v1 |
| #18, #6 | Checkpoints | `wdf_artifactclient.checkpoint` | `write_checkpoint(client, source, producer, tenant, batch, seed, segment, meta, deadline=None, progress=None) -> reference`, `verify_checkpoint(client, reference, deadline=None) -> Verified`, `prune_plan(references, keep) -> list`, and the index queries of `wdf_artifactindex` that give the previous checkpoint | artifactclient v1 |
| #6, #5, #24 | Aggregate index | `wdf_artifactclient.aggregate` | `build_index(entries, header, out)` (entries an iterator sorted by task id, `out` a binary stream, raises `out-of-order`), `put_index(client, entries, header) -> reference` (uploads only, the workflow commits the reference), `read_index(client, reference, tenant) -> iterator of entries`, `open_results(client, entries, scratch) -> iterator`. `python -m wdf_artifactclient.bench --results N [--twice] [--endpoint URL --credentials FILE] [--dsn DSN --jobs N]` exits 0 when the counts are right, 1 when not, and prints `results_put`, `results_verified`, `index_entries`, `index_sha256`, `lists` and the seconds of each phase | artifactclient v1 |
| the cluster, #6 | Reaper and its Schedule | `packages/artifactreaper/` (import `wdf_artifactreaper`) | `reap_once(conn, client, now, windows) -> ReapReport`. `python -m wdf_artifactreaper run-once --dsn DSN --endpoint URL --credentials FILE [--now ISO] [--dry-run]` exits 0, 1 a failed pass, 2 usage, 3 database unreachable, and prints one line per counter (`results_jobs`, `checkpoint_jobs`, `deleted_objects`, `kept_live`, `pruned_checkpoints`, `expired_cache_entries`, `tombstoned`, `purged`, `orphans`). `schedule ensure\|trigger\|worker --temporal-address ADDR`. The Schedule id and the task queue are constants | artifactreaper v1 |
| #15 | Fault hooks | `wdf_artifactclient.faults`, `scripts/artifacts/store-outage.sh` | as the proposals table. Every hook prints what it changed and exits 0, 1 on failure, 2 usage | artifactclient v1 |
| #4 consumers | Reference models that follow the layout, and a generated reference schema | `packages/events/` (`wdf_events.layout`), `contracts/artifact/reference.schema.json` | `wdf_events.layout()` returns the parsed layout read through package data, `python -m wdf_events.schemagen --check` also covers the reference schema | library v1, no API change |
| #3 consumers, #6 | State contract version 2 and the index additions | `contracts/state/`, `packages/artifactindex/` | table `artifact_use (job_id, artifact_row_id, created_at, last_linked_at)`, columns `build_info_uri` and `purged_at` of `artifact` (both expand only, the unique index is unchanged), `link_use` (which revives a tombstoned row and refuses a purged one), `invalidate_cache_entry` and `expired_cache_entries` (which retire the key to the SHA-256 hex of `old_key:row_id`), `uses_of`, `lookup_cache_entry`, `unreferenced_content`, `tombstone`, `purge` (which evaluates `unreferenced_content` again under the row lock), `index_reference` that revives a soft deleted or purged row and returns `Revived`, `checkpoints_of`, `previous_checkpoint`, and the exported schemas `artifact-row/2.json` and `artifact-use/1.json` | state v2, artifactindex v2 |
| #3 consumers, #13, #16, #17 | Retention invariant, inputs, progress tables and grants | `contracts/state/invariants/`, `contracts/state/inputs.yaml`, `packages/stateops/` | invariant `deleted-artifact-of-live-job`, the inputs of the proposals table with the labelled sql form, the tables `reaper_job` and `reaper_cursor`, and the grants of `R__roles.sql` for the use table, the progress tables and the reaper | state v2 |
| #3 and #4 consumers, the cluster | Store contract version 3, the reusable fixture and a second container per session | `contracts/store/roles.yaml`, `wdf_storecheck.fixtures`, `packages/itest/` | `aggregate` gains `get` on `wdf-sources` and `compile` gains `put` and `multipart` on `wdf-results`. The plugin offers `running(t1, product, directory, tmpfs)` and the fixtures `store` (module scoped, both products) and `store_session` (one container for a session, SeaweedFS), with `GOMEMLIMIT=512MiB` set for SeaweedFS. `Session` takes a `containers` count and `Session.container` accepts a `name`, so one session that declares two holds two containers with different names, taking both host lock files up front and retrying with backoff without hold-and-wait. The `t1` fixture declares two when a collected test carries the marker `pair`, and the plugin registers the markers `pair` and `isolated` | store v3 |

### Rules agreed with epics that do not exist yet

These are stated here so the owner epic confirms them in its own spec, and a lane never has to guess.

| Epic | Rule |
| --- | --- |
| #6 Workflow | Calls `commit` for the accepted attempt only, with a client of the `workflow` store role, and retries the attempt when the commit raises `OUTPUT_UPLOAD_FAILED`. Builds the aggregate index in an activity before the aggregate task, from the recorded results read in task id order, with `build_index` and `put_index`, and commits the returned reference. Looks up the cache before placing a compile task, calls `cache.use` on a hit, and fills the cache with `record` after the commit of a compile attempt (decision 18). Treats `ARTIFACT_LOST` of a cache sourced component as an invalidation and a rebuild. Maps the codes of `failures.yaml` into its failure class table. Chooses the previous checkpoint after `CHECKPOINT_CORRUPT` (D7) |
| #10 Workers | Stage inputs, run, collect outputs and write checkpoints through this library with the credentials of the role of the task type, hold no database access, and heartbeat from a separate thread using the `progress` callback |
| #11 Toolchain | Confirms the file names of the staging contract and the schema of `build-info.json`, returns the component and build-info references from a compile task, and writes no database row |
| #24 Dispatch | Stores the manifest, the source bundle, a patched manifest of a resume and a submitter supplied index through `put_content` with its connection and `job_id` and the size cap of the staging contract, which indexes the row first, and links each to the job before the job row is inserted, except the patched manifest, which is linked after the job row exists and before `pending_patch_ref` is set |
| #18 Checkpoint | Sets the format, interval, progress marker and the keep count, which is at least two (the retention loader refuses less), and writes no more than one checkpoint per interval, which bounds the surplus a paused job carries. The metadata fields `format_version` and `progress` are opaque to this epic |
| #13 Observability | Turns the new inputs into metrics and alerts |
| #15 Failure scenarios | Calls the fault hooks and owns the scenarios |

## 3. Lanes (disjoint file ownership)

Package names are proposals.
Lane 0 owns every package it creates until the owning lane takes over the glob, as the method says for stubs.

Departures from the method, stated so that the captain approves them knowingly:

- Lanes R and S start before lane 0 merges, because they edit closed packages and contracts that lane 0 does not touch and that the code lanes need merged first (the store roles and fixture, the key derivation of the models, the index functions and tables).
  The method says the other lanes start only after lane 0 is merged and that lane 0 is the only barrier.
  Here C.1 is a second barrier: every other code lane needs the client core, so C.1 starts alone after lane 0, R.1 and R.2 and the rest follow it.
  The stub mechanism that exists to let lanes run in parallel is therefore used only after C.1, and the critical path is longer than the method's shape (section 7).
- Four issues edit files of closed epics and are `revisit` children of them: R.1 of the envelope epic (#4, and it is the existing issue 215 re-parented), R.2 of the state epic (#3, the store contract and its fixture) with the change of `packages/itest` that belongs to the envelope epic, and S.1 and S.2 of the state epic (the index, the grants, the invariants and the inputs).
  Each adds one dated closure addendum line to the spec of each epic it touches, as the method says.
  The addendum lines of one epic are appended to one list, and a conflict is resolved by keeping every line.
- R.2, S.1 and S.2 bump pinned contracts.
  Each opens its contract file edit (with the generated files that the drift check forces into the same change) as a PR labelled `contract-change` that touches `contracts/` and the model that generates it, with the consumer list of section 2, and its package changes follow in the same issue.
- Lane Needs run across lanes, because a test over a stub would assert only `NotImplementedError` and the library is accepted by the store and the database that it works against: C.1 waits for R.1 and R.2, C.2 for S.1, K.1 for S.1, K.2 for lane 0, P.1 for S.2, K.1 and C.1, P.2 for C.2 and K.3, F.1 for C.1 and K.1.
- Lane 0 pins the contracts as files with their loader, and the skeletons of two packages with stubs, while the code belongs to lanes C, K, P and F.
  A conformance test in each skeleton accepts `NotImplementedError` only for the names of its `STUBBED` list, and the owning issue empties the list.
- The root `pyproject.toml` is edited once, by lane 0 (issue 0.3), to list the two new workspace members in its dependency group and its sources.
- `docs/delivery/method.md` is not edited by this spec.
  Its verifier table still lists `verify-storage` for #12, and line 112 points the exit test at "spec section 7", which is section 6 here.
  The envelope, validator and state specs record the same follow-up.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| R Revisit | 2 | R.1: `packages/events/**`, `contracts/artifact/reference.schema.json`, one closure addendum line of `docs/delivery/epics/03-envelope.md`. R.2: `contracts/store/**`, `packages/storecheck/**`, `packages/itest/**` (no other lane edits `packages/itest` in this epic), `scripts/storage/store/**`, one closure addendum line each of `docs/delivery/epics/05-state-storage.md` and `docs/delivery/epics/03-envelope.md` | R.1, R.2 | nothing (first) |
| S State additions | 1 | S.1: `packages/artifactindex/**`, `contracts/state/{README.md,VERSION,schemas/**}`, `contracts/state/vectors/artifacts/**`, one closure addendum line of `docs/delivery/epics/05-state-storage.md`. S.2: `packages/stateops/**` (including its migrations), `contracts/state/{inputs.yaml,inputs.schema.json,invariants/**}`, `contracts/state/vectors/{inputs,invariants}/**`, one closure addendum line of the same file | S.1, S.2 | S.1 needs R.1 for its last step. S.2 needs S.1 and 0.1 |
| 0 Contracts and skeleton | 1 | `contracts/artifact/{README.md,VERSION,failures.yaml,failures.schema.json,staging.yaml,staging.schema.json,index.schema.json,retention.yaml,retention.schema.json}`, `contracts/artifact/vectors/{failures,staging,index,retention}/**`, `packages/artifactclient/**` and `packages/artifactreaper/**` until the lanes below take over, the root `pyproject.toml` edit and `uv.lock` | 0.1, 0.2, 0.3 | 0.2 needs 0.1, 0.3 needs 0.1 (barrier, merged alone) |
| C Client core | 1 | `packages/artifactclient/src/wdf_artifactclient/{client,errors,keys,commit,staging,__main__}.py`, `packages/artifactclient/README.md`, `packages/artifactclient/tests/test_{client,keys,commit,staging}*.py` | C.1, C.2, C.3 | lane 0, R.1 and R.2 for C.1, C.1 and S.1 for C.2, C.1 for C.3 |
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
- `packages/artifactclient/README.md` is lane 0's until C.1 takes it, and lane C owns it after that, including the per product facts that C.1 records.
  Lanes K and F report their measured facts in their PR and in test output and do not edit it.
- `contracts/artifact/README.md` is lane 0's.
  After lane 0 merges, a file of the pinned contracts changes only through a contract-change PR.
- Migration numbers stay disjoint by construction: the state spec allocates `0300` to `0399` to `packages/artifactindex` (S.1 uses `0301`), gives `packages/stateops` no range, and this spec allocates `0400` to `0499` to it (S.2 uses `0400`).
- `contracts/state/README.md` and `contracts/store/README.md` belong to S.1 and R.2, and S.2 adds its own table rows to the first in the PR that adds the files.
- The addendum lines of `docs/delivery/epics/05-state-storage.md` and `docs/delivery/epics/03-envelope.md` are appended to the list at the end of each, one line per PR.
- The architecture note `docs/architecture/13-state-envelope-artifacts.drawio` and its svg export are not edited by a lane.
  The worker who runs the exit test refines the note and re-exports it with `scripts/export-diagrams.sh` at closure (section 8), as the method's definition of done requires.
- `deploy/versions.yaml`, `justfile`, `just/*.just`, `docs/dev-setup.md`, `deploy/host/ports.yaml`, `.github/workflows/*` and `deploy/**` are not touched.
  Host access to the store and the database goes through `kubectl port-forward` bound to 127.0.0.1, as the state epic does, so no ports row is added.
- Generated files (`contracts/artifact/*.schema.json`, `contracts/artifact/reference.schema.json`, `contracts/state/schemas/**`, `contracts/state/inputs.schema.json`, `R__artifact_kinds.sql`) are committed, the `gen --check` commands regenerate them, and a lane never hand edits one.
- No tracked file holds a credential.

## 4. Issues

Each lane issue below was filed on 2026-11-04 from the [lane issue template](../../../.github/ISSUE_TEMPLATE/lane-issue.md), as a sub-issue of #12 (R.1 as a `revisit` sub-issue of #4 and it is the existing issue #215 re-parented, R.2, S.1 and S.2 as `revisit` sub-issues of #3), and its body holds the scope, owns, acceptance criteria, verifier and needs.
Each issue copies its Owns line from the lane table, names its tests as the Verifier section, and keeps only the `just ci` and Owns checkboxes of Done means.
Done means, for every issue: `just ci` is green on the branch (it runs `just itest`, so every lane's gate needs Docker), the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files of section 3 changed.
Acceptance lines are commands with an observable result, and a line that states a document or a property names the command that shows it.
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
| 8 Retention policy and reaper | 0.2 (the windows), S.2 (the progress tables), P.1, P.2, P.3. The per bucket lifecycle safety nets are delivered |
| 9 Capacity guard | delivered by the state epic, with the full store mapping in C.1 |
| 10 Output tail capture and the decision record | C.3, and section 0.4 |
| 11 Fault injection hooks | F.1 |
| 12 Compile result cache | S.1 (index functions), K.3, P.1 (expiry) |
| 13 Producer recompute | deferred (D4) |
| not in the epic's list: the reference models, the store roles, the container helper, the state additions and the grants | R.1, R.2, S.1, S.2 |

### Consumes and produces per issue

The template's Consumes / produces section of each filed issue is its row below, with the pinned rows of section 2 and the stub names of section 5.

| Issue | Consumes | Produces |
| --- | --- | --- |
| R.1 | artifact v2 layout, `wdf_events.models`, compat v1 | `wdf_events.layout`, the generated `reference.schema.json` |
| R.2 | store v2, `wdf_storecheck`, `wdf_itest`, S8 figures | store v3, `wdf_storecheck.fixtures`, two named containers per session |
| S.1 | state v1, artifactindex v1, R.1 | state v2 tables and columns, the retired key rule, the index additions |
| S.2 | S.1, state v1 inputs and invariants, failures table of 0.1 | the grants, the progress tables, the invariant, the new inputs and the labelled form |
| 0.1 | artifact v2, job v2 registry, S8 figures | `failures.yaml`, `staging.yaml`, the loader and generator |
| 0.2 | 0.1, status v1, state retention | `index.schema.json`, `retention.yaml` |
| 0.3 | 0.1, the package conventions of earlier epics | the skeletons, the workspace edit |
| C.1 | lane 0, R.1, R.2, `wdf_storecheck.s3` | keys, typed errors, the client, `verify` |
| C.2 | C.1, S.1 | `commit`, the connection holder's index-first `put_content`, the zombie test |
| C.3 | C.1, staging contract | `stage_inputs`, `collect_outputs`, `capture_tail`, the stand-in guest |
| K.1 | C.1, S.1 | `write_checkpoint`, `verify_checkpoint`, `prune_plan` |
| K.2 | C.1, 0.2 | the index builder and reader, the bench |
| K.3 | C.2, S.1 | `lookup`, `use`, `record`, `invalidate`, `touch` |
| P.1 | 0.2, C.1, K.1, S.2 | `reap_once` passes 1 to 4, `run-once` |
| P.2 | P.1, C.2, K.3 | passes 5 and 6, the collector and the orphan sweep |
| P.3 | P.1, P.2, `temporalio`, the dev server image | the Schedule, workflow, activity and `schedule` commands |
| F.1 | C.1, K.1 | `faults`, `store-outage.sh` |

### Filed issues

- [#215](https://github.com/AndoNorth/wasm-dag-forge/issues/215) **R.1 Reference models derive their kinds from the layout** Lane R, tier T0, with the events package tests that `just ci` already runs. Needs: nothing. Blocks: S.1, C.1. A `revisit` child of #4.
- [#222](https://github.com/AndoNorth/wasm-dag-forge/issues/222) **R.2 Store contract version 3, the reusable store fixture and a second container per session** Lane R, tier T1, and T2 in the exit test. Needs: nothing. Blocks: C.1. A `revisit` child of #3 (and it touches #4).
- [#223](https://github.com/AndoNorth/wasm-dag-forge/issues/223) **S.1 Artifact use links, cache entry changes and the index additions** Lane S, tier T1. Needs: R.1. Blocks: S.2, C.2, K.1, K.3. A `revisit` child of #3.
- [#224](https://github.com/AndoNorth/wasm-dag-forge/issues/224) **S.2 Grants, progress tables, retention invariant and metric inputs** Lane S, tier T1 and T2. Needs: S.1, 0.1. Blocks: P.1. A `revisit` child of #3.
- [#225](https://github.com/AndoNorth/wasm-dag-forge/issues/225) **0.1 Failure taxonomy, staging and limits contracts** Lane 0, tier T0. Needs: nothing. Blocks: S.2, 0.2, 0.3, C.1.
- [#226](https://github.com/AndoNorth/wasm-dag-forge/issues/226) **0.2 Aggregate index format and reaper windows contracts** Lane 0, tier T0. Needs: 0.1. Blocks: C.1, K.2, P.1.
- [#227](https://github.com/AndoNorth/wasm-dag-forge/issues/227) **0.3 Client and reaper package skeletons** Lane 0, tier T0 and T1 (the wheel test). Needs: 0.1. Blocks: C.1.
- [#228](https://github.com/AndoNorth/wasm-dag-forge/issues/228) **C.1 Streaming put and get with typed errors and retries** Lane C, tier T0 and T1. Needs: 0.1, 0.2, 0.3, R.1, R.2. Blocks: C.2, C.3, K.1, K.2, P.1, F.1.
- [#229](https://github.com/AndoNorth/wasm-dag-forge/issues/229) **C.2 Commit by reference and the zombie test** Lane C, tier T1 (marked `pair`, store and database in one session, through R.2). Needs: C.1, S.1. Blocks: K.3, P.2.
- [#230](https://github.com/AndoNorth/wasm-dag-forge/issues/230) **C.3 Task I/O staging and the output tail** Lane C, tier T1. Needs: C.1.
- [#231](https://github.com/AndoNorth/wasm-dag-forge/issues/231) **K.1 Checkpoints** Lane K, tier T1 (marked `pair`). Needs: C.1, S.1. Blocks: P.1, F.1.
- [#232](https://github.com/AndoNorth/wasm-dag-forge/issues/232) **K.2 Aggregate index object** Lane K, tier T0 and T1. Needs: C.1, 0.2.
- [#233](https://github.com/AndoNorth/wasm-dag-forge/issues/233) **K.3 Compile cache client** Lane K, tier T1 (marked `pair`). Needs: C.2, S.1. Blocks: P.2.
- [#234](https://github.com/AndoNorth/wasm-dag-forge/issues/234) **P.1 Reaper core: results, checkpoints, prune and cache expiry** Lane P, tier T1 (marked `pair`). Needs: 0.2, C.1, K.1, S.2. Blocks: P.2, P.3.
- [#235](https://github.com/AndoNorth/wasm-dag-forge/issues/235) **P.2 Content-addressed collection and the orphan sweep** Lane P, tier T1 (marked `pair`, and `isolated` for the orphan sweep). Needs: P.1, C.2, K.3. Blocks: P.3.
- [#236](https://github.com/AndoNorth/wasm-dag-forge/issues/236) **P.3 Temporal Schedule** Lane P, tier T1 (Temporal dev server container only, the activity under test calls an injected pass function, so the session holds one container). Needs: P.1, P.2.
- [#237](https://github.com/AndoNorth/wasm-dag-forge/issues/237) **F.1 Fault hooks** Lane F, tier T1 (the scenarios are marked `isolated`, the table test is T0), and T2 in the exit test. Needs: C.1, K.1.

## 5. Order, stubs and spikes

- First, in parallel: R.1, R.2, S.1 (its last step waits for R.1) and 0.1.
  Then 0.2, 0.3 and S.2 in parallel.
  Lane 0 (0.1, 0.2, 0.3) is merged alone, with an estimate of three days, and R.1 and R.2 merge before C.1 starts, while S.1 merges before C.2, K.1 and K.3 start.
- After lane 0, R.1 and R.2: C.1 starts alone, because every other code lane needs the client core (the second barrier of section 3).
  Then C.2, C.3, K.1 and K.2 start together, K.3 follows C.2, and P.1 follows K.1 and S.2.
  P.2 follows P.1, C.2 and K.3, P.3 follows P.2, and F.1 follows C.1 and K.1.
  Then the exit test.
- DAG: ((R.1 ‖ R.2 ‖ S.1 ‖ 0.1), then (0.2 ‖ 0.3 ‖ S.2)), then lane 0 merged, then C.1, then (C.2 ‖ C.3 ‖ K.1 ‖ K.2 ‖ F.1 after K.1), then (K.3 after C.2 ‖ P.1 after K.1 and S.2), then P.2, then P.3, then the exit test.
- Every issue needs only T0 and T1.
  Cluster using work is the T2 half of R.2, S.2 and F.1 and rows 9 to 17 of the exit test, which take `flock /tmp/wdf-cluster.lock`, and T1 work waits for the container slots of `wdf_itest`.
- The five questions of the draft are answered (decisions 16 to 20), so no lane waits on the captain.
  The defaults D1 to D8 stand, and a change to one before its issue starts changes only the issues in its last column in section 0.3.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| The module functions of the two skeletons that 0.3 creates | `NotImplementedError("not implemented: owned by lane C")` and the same for K, P and F | each package's `test_stubs.py` accepts that error only for the names in its `STUBBED` list and the real behaviour for the rest, and the owning issue empties its names |
| The stand-in guest and worker of `wdf_artifactclient.testing` | a Python callable that reads `input/` and writes `output/`, standing for the guest of #11 and the runner of #10 | the staging tests are parametrized by the runner, and #10 and #11 add their real runner to the same parameters |
| The stand-in record activity | a test function that calls `commit`, `record`, `cache.use` and `put_index` for a hand chosen accepted attempt, standing for the workflow of #6 | #6 runs the zombie, cache and paused job tests with its real activity |
| The reaper's Schedule constants (id and task queue) | names that the workflow epic has not pinned | the workflow epic replaces them and a test compares them with its naming contract |
| Stand-in login users for the group roles | a login created by the test and added to `wdf_reaper` or `wdf_artifacts` | the service epics create the real logins and receive the grants tested here |

Spikes: none.
The object counts and heap bound are S8's, and the product facts are S5's.
Three facts are measured by lanes and recorded, not gated: whether each product honours `If-None-Match` and an MD5 entity tag for a single part (C.1), the put and read rates of the client for 100,000 small results (K.2 and row 8), and the largest heartbeat gap during a 256 MiB transfer (C.1).

## 6. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main` taken outside any git repository, with Docker running and the cluster free.
Each command runs from the export root as written, its real output is pasted in the PR that records the run, and it is summarised in Closure.
Rows 9 to 17 take the cluster lock for their whole sequence, for example `flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 bash -c '<commands>'`, because `just up`, `just down` and `just nuke` take the lock themselves unless that variable is set.
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
| 5 | `pytest packages/artifactclient packages/artifactreaper packages/events packages/artifactindex packages/stateops packages/storecheck packages/itest -m "not isolated" -q -rs`, then `pytest -m isolated packages/artifactclient -q -rs` and `pytest -m isolated packages/artifactreaper -q -rs` | all pass with no `SKIPPED` line, the client core tests on both object store products and the tests that need a store and a database together on SeaweedFS, and each isolated invocation passes alone with a store of its own |
| 6 | `pytest packages/artifactclient packages/artifactreaper packages/artifactindex -v -k "zombie_upload_changes_nothing or flipped_byte_is_corrupt_then_lost or multipart_abort_leaves_no_parts or first_writer_wins_between_two_builds or record_again_after_invalidation_and_after_expiry or paused_and_admitted_jobs_are_never_touched_by_any_pass or running_job_is_pruned_to_the_keep_count_and_keeps_its_newest or reap_races_resubmit or a_paused_jobs_inputs_are_kept or interrupted_write_is_invisible or store_outage_during_verify or out_of_order_or_repeated_entries_are_refused or foreign_tenant_result_is_refused or aggregate_never_lists or ensure_twice_leaves_one_schedule or every_code_of_the_taxonomy_has_a_scenario or heartbeat_gap"` | each named test is listed `PASSED` |
| 7 | `pytest packages/artifactclient packages/artifactreaper packages/events packages/artifactindex packages/stateops packages/storecheck -q -rs -k wheel` | all pass, none skipped, so every package works from a built wheel |
| 8 | `python -m wdf_artifactclient.bench --results 100000 --twice` (starts its own SeaweedFS container with the heap bound when no endpoint is given) | exit 0, `results_put: 100000`, `results_verified: 100000`, `index_entries: 100000` and `lists: 0` for both runs and the same `index_sha256`, with the phase times printed for the record and no timing guard |
| 9 | `scripts/tilt/run.sh ci` after `just up`, inside the locked sequence | exit 0 with `appdb`, `temporaldb` and `objectstore` healthy and `objectstore-bootstrap` and `appdb-migrate` completed |
| 10 | `python -m wdf_storecheck run --endpoint http://127.0.0.1:<port> --credentials <state file> --mode base`, then `--mode access` against the deployed store through a port-forward | exit 0 for each, with no `FAIL` line, and for `--mode access` an `ok access matrix aggregate: <n> cells` line and an `ok access matrix compile: <n> cells` line, where the cells are derived from `roles.yaml` and so include the two new grants |
| 11 | `python -m wdf_artifactclient.bench --results 2000 --endpoint http://127.0.0.1:<port> --credentials <state file> --dsn DSN --jobs 2` | exit 0, `results_verified: 2000`, and two jobs seeded, one advanced to `succeeded` and one left `running`, each with committed results and a checkpoint |
| 12 | `python -m wdf_artifactreaper run-once --dsn DSN --endpoint http://127.0.0.1:<port> --credentials <state file> --now <timestamp 10 days ahead>`, then the same command again | exit 0, `deleted_objects` above 0, `results_jobs: 1` and `checkpoint_jobs: 1` for the succeeded job, `kept_live` for the running one, then `deleted_objects: 0` on the repeat |
| 13 | `python -m wdf_artifactclient verify ... REF.json` on a result reference of the running job, then of the succeeded job | exit 0 for the running job, and exit 1 with `code: ARTIFACT_LOST` and `cause: ARTIFACT_NOT_FOUND` for the reaped one |
| 14 | `python -m wdf_artifactclient.faults flip-byte --endpoint http://127.0.0.1:<port> --credentials <state file> --role aggregate --uri <a second result of the running job, not the one that row 15 uses>`, then `verify` on its reference | exit 0 for the hook and exit 1 for `verify` with `code: ARTIFACT_LOST` and `cause: ARTIFACT_CORRUPT` |
| 15 | `scripts/artifacts/store-outage.sh stop`, then `verify` on the reference of the running job, then `scripts/artifacts/store-outage.sh start` and `verify` again | the first `verify` exits 1 with `code: STORE_UNAVAILABLE` in under 60 s, and the second exits 0 once the store is back |
| 16 | `python -m wdf_stateops.invariants --dsn DSN` | exit 0, so no job that can still run lost an artifact to the reaper, and the two workflow comparisons print `skipped:` lines |
| 17 | `python -m wdf_stateops.inputs --dsn DSN --store-endpoint http://127.0.0.1:<port> --credentials <state file>` | a value for every input of `inputs.yaml`, including `artifact_failures` (one value per code), `checkpoint_age_seconds`, `reaper_deleted`, `reaper_overdue`, `cache_hits` and `cache_misses` |
| 18 | `just doctor`, then `just down` and `just nuke` twice | exit 0 for each, and `just nuke` leaves nothing and repeats cleanly |
| 19 | `just ci` | exit 0 |
| 20 | the `ci` and `state-modules` workflows on `main` | one green run of each, the URLs recorded in Closure |

Verifier delta: none, per recorded decision 1.

Not provable by this epic: the real staging by a worker and a guest (#10, #11), the zombie test with Temporal's own fencing and a real workflow recording references (#6), heartbeats under a real Temporal activity, the reaper as a deployed pod and its Schedule on the cluster's Temporal (the workflow epic, which needs a service image), the `Days` lifecycle rule on aged objects and the abort of incomplete multipart uploads by the store's lifecycle job (#16), a store outage or a corruption drill beyond the hooks and a restart while full (#15), checkpoints of a multi-day job and their real format (#18), producer recompute, retention behaviour over a multi-day soak, a mitigation of the cache's existence side channel, the accepted risk that a dispatch resubmit of identical bytes of a manifest or source bundle (an object more than the orphan grace old with no live row) races the orphan sweep at that moment and loses an input that has no rebuild, the narrow window of the orphan sweep for an object with no row that is uploaded again and committed at the moment it is deleted, prefix level restriction of a role, transfer behaviour over a real network, and a maximum job duration above 7 days.

## 7. Parallelism and cost

- Maximum parallel workers: 4 for the first phase (R.1, R.2, S.1 and 0.1), then 3 (0.2, 0.3 and S.2), then 1 for C.1, then up to 5 (C.2, C.3, K.1, K.2 and F.1), planned at 4 because the host runs two T1 containers at a time, then 2 for K.3 and P.1, then 1 each for P.2 and P.3, then 1 for the exit test.
- Host constraints: T0 work needs only `uv` and Python from the dev shell.
  T1 work starts SeaweedFS, RustFS, TimescaleDB and one Temporal dev server by `docker run` on 127.0.0.1, each capped at 1 GiB, with at most two containers across the host, and a session holds two only after R.2 and only when it declares them.
  Two sessions that each declare two run one after the other, so parallel lanes that need a store and a database together queue for the host.
  T2 work needs the `ci` cluster, and a worker never starts `dev` or `soak`.
- CI cost, stated as estimates: no new workflow and no new image (recorded decision 15), so the added cost is T1 containers in every lane's `just ci`.
  The plan keeps it bounded.
  A session that collects a test marked `pair` takes both host container slots up front, and holds at most the session scoped SeaweedFS of `store_session` and one module scoped database container, so it never holds more than two containers.
  The client core tests run on both products in module scoped containers beside the session store, never a third, and every other module of the new packages shares the session store and isolates itself by tenant name and by deleting only what its own rows name.
  Tests that need a store of their own (the full store, the stopped store, the fault scenarios and the orphan sweep, which lists a whole bucket) are marked `isolated` and run in separate invocations, `pytest -m isolated`, each holding a store and at most one database container, one invocation per package, named in the acceptance of their issue and in exit row 5.
  They are not part of `just itest` or `just ci`, which would otherwise start three containers in one session, so the honest CI cost is the shared session, and the isolated set is paid in the exit test and in each lane's PR evidence.
  The session store starts once per `just itest` run, and the isolated invocations start a store each.
  The database fixture adds a TimescaleDB container per module that needs one, as the state epic's tests already do, and the schedule module adds one Temporal dev server and an image pull of an estimated few hundred MiB on a clean runner.
  The 100,000 result bench runs only in the exit test, and `just ci` runs the 1,000 result form.
  No test waits for a timer: the reaper takes `now` as an argument and the Schedule test triggers it.
- Estimates, not measurements, in worker-days: R.1 (1), R.2 (2), S.1 (3.5), S.2 (2.5), lane 0 (3), C.1 (4), C.2 (2), C.3 (2.5), K.1 (2.5), K.2 (2.5), K.3 (1.5), P.1 (3.5), P.2 (3.5), P.3 (2), F.1 (1.5), exit test (1.5).
  That is about 39 worker-days of effort and about 20 days elapsed along lane 0, C.1, K.1, P.1, P.2, P.3 and the exit test (3 + 4 + 2.5 + 3.5 + 3.5 + 2 + 1.5), with the cluster lock, the container slots, `uv.lock` rebases and the second barrier at C.1 as the main sources of slip.

## 8. Closure

Run on 2026-11-06 against `872d53bee75a50acc45d1bb8466d68bc9d122c28`, the commit of the last lane merge (P.3), from a `git archive origin/main` extracted into a scratch directory with no enclosing git repository.
Rows 1 to 4, 6 to 10, 12 to 20 pass, with departures on rows 12 to 14, 16 and 17 that the table states.
Row 5 fails (11 tests of `packages/itest` when collected beside the session store, filed as [#258](https://github.com/AndoNorth/wasm-dag-forge/issues/258)) and row 11 cannot run as written (the bench has no `--dsn` and `--jobs` form, filed as [#259](https://github.com/AndoNorth/wasm-dag-forge/issues/259)).
The epic was therefore not closed by this run, and the pull request that recorded it did not close the epic issue.
The pasted output of every row is in that pull request.

Method of the run:

- The worker that ran it wrote none of the lanes.
- The host is the 20 GiB WSL2 VM with Docker running.
  Rows 9 to 18 held `/tmp/wdf-cluster.lock` for their whole sequence and ran with `WDF_CLUSTER_LOCK_HELD=1`, and the cluster of other work on the host was not touched.
- Row 3 edited `contracts/artifact/layout.yaml` in the export and restored it from a copy, and every generated file was regenerated from the restored layout, so the export again equals the archive.
- No container of the run was left behind, and `just nuke` after row 17 left no node, no registry and no data directory.

Results:

| # | Result | Evidence |
| --- | --- | --- |
| 1 | pass | `uv sync --frozen` exit 0, `uv lock --check` exit 0 with 57 packages resolved |
| 2 | pass | no `DRIFT` line for the five packages |
| 3 | pass | with a `report` kind added, `schemagen --check` exit 1 naming `contracts/artifact/reference.schema.json` (and the `task.requested` schema that embeds it), `wdf_artifactindex.gen --check` exit 1 naming `R__artifact_kinds.sql` (and `artifact-row/2.json`), the library accepted the `report` reference before the regeneration, and both checks exit 0 after it and after the restore |
| 4 | pass | the three valid files, the 12 valid reference vectors and `roles.yaml` exit 0, and all 41 invalid files (5 failures, 15 index, 11 retention, 10 staging) and the 14 invalid reference vectors exit 1 with a `reason:` line that names the directory they sit in |
| 5 | fail | 881 passed, 11 failed and 2 skipped in the first invocation, see [#258](https://github.com/AndoNorth/wasm-dag-forge/issues/258), and both isolated invocations pass (9 and 6 passed). The two skips are the `STUBBED` parametrizations that are empty once every stub is replaced |
| 6 | pass | 69 passed, each of the 17 named tests is listed `PASSED` |
| 7 | pass | 10 passed, none skipped |
| 8 | pass | exit 0, both runs `results_put: 100000`, `results_verified: 100000`, `index_entries: 100000`, `lists: 0` and `index_sha256: c227f3eca53c7549c9a47f6b5ab40dc480fde02cfd4e9912bcdfe502064b7f5f`, phase times in the next section |
| 9 | pass | exit 0, `SUCCESS. All workloads are healthy.`, `appdb-0`, `temporaldb-0` and `objectstore-0` running and every `appdb-migrate` file applied, with the bucket rules printed by `objectstore-bootstrap` |
| 10 | pass | `14 of 14 base checks passed`, and the access mode has no `FAIL` line, with `ok access matrix aggregate: 20 cells` and `ok access matrix compile: 20 cells` |
| 11 | fail | exit 2, `unrecognized arguments: --dsn ... --jobs 2`, see [#259](https://github.com/AndoNorth/wasm-dag-forge/issues/259). Rows 12 to 17 used two jobs seeded by a scratch script built on the reaper test world, 2,000 results and two checkpoints each, one `succeeded` and one `running` |
| 12 | pass, with a departure | first run `results_jobs: 1`, `checkpoint_jobs: 1`, `deleted_objects: 2004`, second run `deleted_objects: 0`. The running job had two checkpoints, so `kept_live` was 0, and a second running job with five checkpoints gave `kept_live: 2` and `pruned_checkpoints: 3` |
| 13 | pass | exit 0 for the running job, exit 1 with `code: ARTIFACT_LOST` and `cause: ARTIFACT_NOT_FOUND` for the reaped one |
| 14 | pass, with a departure | `--role simulate` as the row first read exits 1 with `AccessDenied 403` because `simulate` has no `get` on `wdf-results`, so the row now names `aggregate`, which flipped a byte (exit 0), and `verify` then exits 1 with `code: ARTIFACT_LOST` and `cause: ARTIFACT_CORRUPT` |
| 15 | pass | `verify` while stopped exits 1 with `code: STORE_UNAVAILABLE` after 7 s, and exit 0 after `store-outage.sh start` and a new port-forward |
| 16 | pass | exit 0 with `skipped: open-workflow-without-live-row` and `skipped: running-without-workflow` |
| 17 | pass | a value for each input, among them one `artifact_failures` value per code, `checkpoint_age_seconds`, `reaper_deleted`, `reaper_overdue`, `cache_hits` and `cache_misses` |
| 18 | pass | `just doctor`, `just down`, `just nuke` and a second `just nuke` all exit 0 |
| 19 | pass | `just ci` exit 0 in the export, 1,724 passed and 2 skipped in `just test`, 411 passed in `just itest` |
| 20 | pass | the `ci` run [42256475026](https://github.com/AndoNorth/wasm-dag-forge/actions/runs/42256475026) and the `state-modules` run [42256474951](https://github.com/AndoNorth/wasm-dag-forge/actions/runs/42256474951), both success on `872d53b` on `main` |

Measured rates of the client for 100,000 small results (a SeaweedFS container with the heap bound, no timing guard):

| Run | Put | Index build | Verify |
| --- | --- | --- | --- |
| 1 | 276.75 s, 361 per second | 2.99 s | 215.17 s, 465 per second |
| 2 | 261.24 s, 383 per second | 2.75 s | 216.21 s, 462 per second |

Per product facts of the client core (recorded by `test_product_facts_if_none_match_and_the_md5_entity_tag`):

| Product | `If-None-Match: *` on a put of an existing object | `If-None-Match: *` on a multipart completion | Entity tag of a single part put |
| --- | --- | --- | --- |
| SeaweedFS | honoured, 412 | honoured, 412 | MD5 of the bytes |
| RustFS | honoured, 412 | honoured, 412 | MD5 of the bytes |

Decision record for observability (#13):

- Every artifact failure surfaces as a `task.attempt.failed` event whose `reason` starts with `<CODE>: `, and the input `artifact_failures` counts them by code, so #13 builds its metric and alert from that input and not from a new event field.
- The reaper writes `reaper_deleted` and `reaper_overdue`, and the checkpoint lag is `checkpoint_age_seconds`, all inputs of `contracts/state/inputs.yaml`.
- The compile cache surfaces as `cache_hits` and `cache_misses`.
- The last 64 KiB of the guest's output is stored as kind `log-tail`, name `tail.log`, even for a failed attempt, and Loki owns the full logs.

What the architecture note now shows: `docs/architecture/13-state-envelope-artifacts.drawio` and its re-exported SVG state that a worker returns references and the workflow commits the accepted attempt, that a job's inputs are linked to it, that results and checkpoints are verified on read, that the reaper is a Temporal Schedule and the only role that deletes, and that cache entries expire by last use.

What moved to which epic:

- the workflow's use of `commit`, `record`, `cache.use` and the index activity, the zombie test with Temporal's own fencing, and the failure class mapping (#6)
- the real worker, staging and heartbeats (#10), the guest and the `build-info.json` schema (#11), and the checkpoint format (#18)
- the dashboards and alerts over the new inputs (#13), the store outage and corruption scenarios (#15) and the characterization soak (#16)
- the deployment of the reaper's worker as a pod, with the service image (#6)
- the golden `job.accepted` vector class (#24), per recorded decision 14

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/delivery/method.md](../method.md#revisiting-a-closed-epic)).

- 2026-11-06: [#258](https://github.com/AndoNorth/wasm-dag-forge/issues/258) makes the container accounting tests of `packages/itest` independent of the session store, so row 5 passes as written.
- 2026-11-06: [#259](https://github.com/AndoNorth/wasm-dag-forge/issues/259) decides whether the bench gains the `--dsn` and `--jobs` seeding form of row 11 or the spec moves it to a script.
