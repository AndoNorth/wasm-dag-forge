# Epic #3: Durable state and artifact storage - breakdown

Status: draft
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/3
Architecture note: docs/architecture/13-state-envelope-artifacts.drawio
Depends on (closed epics): Dev environment (#1), Fixed-resource cluster (#2), Job event envelope (#4), DAG configuration schema and validator (#5)

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-11-01) while preparing this spec, `[read]` means read and not re-run.
The epic issue body was read on 2026-11-01, and this spec was written against that version.
`main` was at `dfaa8f4`, the merge that followed the closure of the validator epic.
Method: [docs/method.md](../method.md).
Models: [docs/epics/04-validator.md](04-validator.md) (the most recent, fully delivered breakdown), [docs/epics/03-envelope.md](03-envelope.md) and [docs/epics/02-cluster.md](02-cluster.md).
Spike results consulted: [object store](../spikes/object-store.md) (S5), [Temporal](../spikes/temporal.md) (S3, S3b), [footprint](../spikes/S2-footprint.md) (S2) and [broker client](../spikes/broker-client.md) (S4) for the RabbitMQ figure.
Tiers, as the earlier breakdowns use them: T0 is host Python with no runtime, T1 is containers started through `wdf_itest` (at most two containers across the host, one at a time per session), and T2 is the cluster from `just up` on preset `ci` under `/tmp/wdf-cluster.lock`.
A test that needs a container carries the `integration` marker and so runs in `just itest` and in every lane's `just ci`.
A T2 check is a command and never a pytest test, so `just ci` never needs a cluster.

## 0. Decisions for the captain (max 5)

### 0.1 Recorded decisions

Everything in the epic body stands.
The calls below were made for earlier epics or by the foundation review of everything merged so far, and are applied here without a new question.

| # | Decision | Source | Applied in |
| --- | --- | --- | --- |
| 1 | No verifier skill, no feature files and no verify recipes for this epic, and the `verify-storage` row of the method (`docs/method.md` lists it for #3 and #12 and says a skill is created in the lane 0 of the first epic that needs it) is dropped. The product tests stay: the schema drift checks, the table conformance tests, the status vocabulary test, the compare-and-set concurrency test, the replay and clock tests of the event history, the S3 contract script and the invariant queries | the captain's standing rule that these epics carry no verifiers. The envelope epic (decision 3) and the validator epic applied the same rule to `verify-job-model`, and neither decision covers `verify-storage`, so this row is this spec's own application of the standing rule, which the captain confirmed on 2026-11-01 | sections 3, 4 and 6 |
| 2 | Python models are the source of anything with a schema, and the exported JSON Schemas are checked in with a drift test | the envelope epic, decision 5, and the brief | 0.3, section 2 |
| 3 | `just` stays the entrypoint with logic in scripts. This epic adds no recipe: migrations run from `scripts/storage/db/migrate.sh` through the helper module `appdb-migrate`, tests run through `just test`, `just itest` and `just ci`, and the recipe table stays at 17 names | the dev environment epic, carried | sections 3 and 4 |
| 4 | Epics are local-development and declarative: no cloud service, no operator and no controller, every component is a module under `deploy/` that Tilt renders | the brief | sections 1 and 4 (D.1, D.2, O.1) |
| 5 | The status contract has ten statuses, with `partial` terminal, while the epic body lists nine (accepted, admitted, running, paused, resume-pending, succeeded, failed, cancelled, terminated). The epic body's list is amended by this spec to the ten of the contract, which the envelope epic pinned when the captain added `partial`, and the issue text is corrected when the lane issues are filed. The status vocabulary, the table `CHECK` constraint and the transition table are generated from `contracts/status/status.yaml`, never retyped, with a generator whose `--check` fails on drift | foundation review, call 1, and the envelope epic, decision 2 | J.1, J.2, 0.3 |
| 6 | The attempt number is the workflow's own per-task attempt counter, monotonic across retries, resumes and patches, as the events and artifact contracts already say. The attempt-scoped object key layout and the event id rely on it. The store never infers, defaults, counts or increments an attempt: every attempt column holds what the event or the reference carries, has no `DEFAULT`, and a test asserts it | foundation review, call 2 | E.1, A.1, 0.3 |
| 7 | The maximum job duration stays 7 days for the PoC (`contracts/job/limits.yaml`, `MAX_JOB_DURATION_DAYS` of `wdf_compat.window`, with the compat reader window of 7 plus 14 days). Every retention and sizing number below is derived from 7 days, and the 30-day cases are cut. Scaling beyond 7 days is a recorded follow-up of section 8 and not work here | foundation review, call 3 | 0.3 (retention), 0.1, M.1 |
| 8 | The class of a job is declared by the submitter, with the default `short` and an upper bound that the validator checks. This supersedes decision 8 of the validator epic ([docs/epics/04-validator.md](04-validator.md)), which derived a class hint from the critical path bound and let the submitter's override win. The shipped validator has only the unknown-class check (`unknown-class` for a `class_override` outside `classes`) and no upper-bound check, so that check is a follow-up outside this epic (section 8), and until it exists the declared class is held only by the `classes` check of the row. The accepted-jobs row keeps one class column, `job_class`, and no `class_override` column: the submit path (#24) fills `job_class` from the `class_override` field of the per-job `JobParams` block and uses `short` when that field is absent, and admission (#8) reads `job_class` from the row. The accepted event also carries a required `jobclass` attribute (`contracts/events/README.md`, `wdf_events.models.Event`). The submit path computes the class once, writes it to `job_class`, and sets that `jobclass` attribute and any `class_override` that it passes on to the same value, so a job never has two classes, and Q.1 reports an accepted event whose `jobclass` differs from its row. The `class_override` field keeps its name, and renaming it belongs with the upper-bound follow-up. Recorded mismatch, changed in no contract here: the golden `job.accepted` vector carries `jobclass: simulate`, which is outside `classes: [short, long]` of `contracts/job/limits.yaml`, so the repository does not tie the event attribute to the validator's class vocabulary (follow-up in section 8). No code in this epic derives a class: nothing here calls `wdf_plan.class_hint` | foundation review, call 4, relayed with the review of this spec on 2026-11-01, superseding the captain's call of 2026-10-31 that the validator epic records | 0.3, J.1, section 8 |
| 9 | The infra node is too small for the app Postgres with TimescaleDB plus RabbitMQ. The `ci` infra node is 3,072 MiB with about 2,600 MiB allocatable and the `wdf-infra` quota is 2,756 MiB [ran: `scripts/cluster/namespaces.sh render ci` prints `limits.memory: 2756Mi`, and 3,764 MiB for `dev`], and Temporal, its Postgres and the object store stand-ins already take 2,314 MiB per spike S2. The real app Postgres, TimescaleDB and the object store are measured at the start of this epic (issue M.1), and the `ci` and `dev` presets and the quota are resized then (issue R.1). Both are early steps that the module lanes need | foundation review, call 5 | M.1, R.1, section 5 |
| 10 | `just up` never applies the namespaces and quotas script today [read: the cluster closure and `scripts/up.sh`]. This epic adds modules that need the quota in force, so issue R.1 makes `just up` apply it, and no module lane starts before R.1 | foundation review, call 6 | R.1, D.1, D.2, O.1 |
| 11 | Runtime libraries read their contract files through package data (`importlib.resources`), never by repository path, as `wdf_events` now does with its `contract/` links. Every new package ships what it reads, including its migrations, as package data, and a wheel test imports and uses the built wheel from a clean virtual environment | foundation review, call 7 | 0.1, 0.3, section 3 |
| 12 | Epics 1, 2, 4 and 5 are merged, so this spec consumes their real contracts, libraries and versions as pinned in section 2 | the brief | section 2 |
| 13 | One content-addressed key space is shared across tenants, because the artifact contract at version 2 has no tenant in `sha256/<hash2>/<hash>`. The existence side channel is owned by the artifact epic (#12). The epic's open question on this is therefore answered by the pin | `contracts/artifact/` artifact v2 | 0.3, O.1 |
| 14 | A single status column with guarded transitions is enough: `pausing` and `cancelling` are views over a requested action that is still outstanding and are never stored, so the row carries a requested action beside the status. The epic's open question on a separate requested status is therefore answered by the pin | `contracts/status/` status v1 | 0.3, J.2 |
| 15 | The object store is SeaweedFS 4.51 pinned by digest, with RustFS 1.0.4 as the tested second target of the same contract script, as the epic body decides from spike S5 | the epic body | 0.2, O.1, C.1 |
| 16 | Decision: the batch bench asserts counts and idempotence and has no timing guards; alternative: guards at three times the medians that M.1 records; why: the epic asks for no performance gate, tripling a measured rate gives a floor and not a ceiling, and the measured rates are recorded in the PR and in Closure instead | review finding on the bench guards | Q.2, M.1 |
| 17 | Decision: the group roles and grants are one hand written repeatable file, `R__roles.sql`, with no `writers.yaml`, schema, vectors or generator; alternative: generate the file from a `writers.yaml` contract; why: about eight `CREATE ROLE` statements do not justify a contract with no upstream source, the writers table of `contracts/state/README.md` already states who writes what, a catalog test holds the file to it, and a repeatable idempotent file is the only form that survives re-application and the cluster wide scope of roles | review findings on the writers file and on the numbered roles migration | Q.4 |
| 18 | Compile-cache entries are rows of the one artifact index table, as the epic body decides ("not a second store"): one table `artifact` with a `kind` column that includes `compile-cache` and a unique key on the cache key. The alternative, a second `compile_cache` table with a foreign key to the wasm row (the first draft of this spec), is not taken. Reasoning: the epic body states the rule, a lookup is one indexed read, and #12 reads one index with its reference count. The cost is nullable cache columns held by `CHECK` constraints | the captain, agreed on 2026-11-01 | 0.1, A.1, Q.1 |
| 19 | An `indexer` object store role writes only `wdf-sources` (the aggregate index objects that the epic body lists as written there), and an `export` role reads `wdf-results` (the evidence export that the epic body lists as a reader). The alternative, giving the index builder the `submit` credentials and letting the export reuse `workflow`, is not taken. Reasoning: the per-role split exists to avoid the over broad grant, and the epic body names both users. The read side of the index builder is stated by #12 when the builder exists, and this epic grants it none | the captain, agreed on 2026-11-01 | 0.2, C.1, O.1 |


### 0.2 Open questions

None of these blocks the start of M.1, lane R or lane 0.
Each has a default that the lanes build on when there is no answer, and an answer that differs from the default changes only the issues named in the last column.

| # | Question | Options | Recommendation | Default if no answer | Changes |
| --- | --- | --- | --- | --- | --- |
| 1 | Which retention windows does the safety net use? The epic leaves windows per job class and the longer keeping of failed and paused jobs open. The event history can only be cut by time (a hypertable retention policy drops whole chunks) and a bucket rule can only expire by age, so neither can keep a failed job longer than a succeeded one. That choice stays with the reaper of the artifact epic (#12), which acts per job. | (a) windows tied to the 7 days: events 14 days, results hard expiry 28 days, checkpoints hard expiry 14 days, a rule that every drop or expiry window is at least twice the maximum job duration, (b) three times as long: events 21, results 42 and checkpoints 21 days, (c) as (a) with a separate shorter window for checkpoints of 10 days | (a). Twice the maximum covers a full-length run followed by a pause of the same length, which is the longest job the PoC supports. Anything shorter risks expiring the checkpoints of a paused job, and (b) only costs disk that the soak host has not yet been chosen to carry. A pause longer than the checkpoint window is outside the PoC, and the checkpoint epic (#18) decides whether a pause expires. | (a) | 0.1, 0.3, E.1, O.1 |
| 2 | Which migration tool? The epic asks for versioned migrations from day one with expand then contract. | (a) numbered plain SQL files with a small Python runner in the workspace (`wdf_migrate`): a `schema_migration` table with checksums, an advisory lock, repeatable files for generated SQL and a lint for destructive statements, (b) Alembic, (c) a binary tool (dbmate or golang-migrate) added to the flake | (a). No ORM was decided, `psycopg` is already locked, so (a) adds no dependency and no tool to the dev shell. Repeatable files let the status vocabulary and the retention policies be generated from their contracts and re-applied when the contract moves. The cost is a few hundred lines that this epic owns. | (a) | 0.3, and every migration issue |
| 3 | Are per-service database roles worth building in a proof of concept? The epic lists it as optional. | (a) group roles and column grants now, as `NOLOGIN` roles from a hand written repeatable migration with a test per role, and login roles and credentials left to the service epics that exist later, (b) defer all of it, (c) full roles now, with login users and a generated credential per role | (a). The grants cost one hand written file and one test, they turn "one writer per kind of change" from a convention into something the database refuses, and retrofitting them after the services exist means touching every service. Credentials are the expensive part and need a service to use them, so they wait. | (a) | Q.4 (dropped for (b), extended for (c) with D.1) |
| 4 | How is the SeaweedFS gap handled, where a credential with `Write` on a bucket can also replace that bucket's lifecycle rules (RustFS refuses it)? The epic defers the choice to the contract script issue. | (a) accept and document it: bucket credentials belong to platform pods only, the contract test records it as a `known-gap` per writing role and fails if the behaviour changes either way, (b) change the default product to RustFS 1.0.4, which loses the delete-while-full behaviour and the field exposure that the epic ranks higher, (c) put a policy proxy that refuses the lifecycle call in front of the store | (a). The PoC runs no tenant code with store credentials, the gap needs a credential that only platform pods hold, and (c) adds a component to a fixed-size cluster to close it. The `known-gap` line makes the choice visible and flips to a failure the day an upstream fix lands. | (a) | 0.2, C.1, O.1 |
| 5 | Does a CI job run the cluster checks of this epic? The T2 checks (modules healthy, store contract against the deployed store, lifecycle expiry) need `just up` and a runner that holds the `ci` preset, which the `kind-smoke` job already does for every pull request. | (a) no new job: T2 evidence comes from the exit test and from lane workers under the cluster lock, (b) extend `kind-smoke` to deploy these modules and run the contract script, which adds minutes to every pull request, (c) a separate workflow `state-modules`, triggered on pull requests that touch the paths of this epic and on pushes to `main` | (c). It gives the epic a green run on `main` as evidence, it guards the three module directories and the contract script against rot, and it costs a runner only when these paths change. | (c). An answer of (a) drops issue X.1, the `state-modules` run of row 23 of the exit test and the matching line of Closure, and nothing else. | X.1 |

### 0.3 Proposals the lanes pin

These answer the open questions and open design items of the epic.
Each is a default that the captain may change in the one review, and none needs an answer to start.
Names, columns and numbers are pinned by lane 0 and the table issues, and change afterwards only through a contract-change PR.
After lane 0 merges, `contracts/state/README.md` and the contract files are the source and this section is only a pointer to them, and Closure replaces its tables with links.

| Item | Proposal | Epic question |
| --- | --- | --- |
| Table `accepted_job` | One row per job. Columns: `job_id uuid` (primary key, the value of `wdf_keys.job_id(tenant, submission_key)`), `tenant text`, `submission_key text`, `batch_id uuid`, `priority_class text`, `job_class text` (the one class column: the declared class that the submit path copies from `JobParams.class_override`, `short` when that is absent, and sets on the accepted event's `jobclass` as well, checked against `classes` of `contracts/job/limits.yaml`), `status text` (default `accepted`), `manifest_ref jsonb` (the artifact reference of kind `manifest`) with a generated `manifest_sha256`, `schema_version text` (the `dataschema` of the accepted event), `traceparent text`, `tracestate text` (null), `published_at timestamptz` (null until the broker confirmed), `seed bigint` (0 up to below 2^53), `parameters jsonb` (checked to be an object), `fingerprint text` (64 lower-case hex), `requested_action text` (null, one of pause, resume, cancel, terminate), `control_seq integer` (default 0), `control_delivered_at timestamptz` (null), `pending_patch_ref jsonb` (null), `created_at`, `updated_at` and `terminal_at timestamptz` (null, set by the guarded transition into a terminal status, which the artifact epic's reaper reads as its clock). `unique (tenant, submission_key)` is the one dedup point and no retention rule ever touches this table. | 3, 11 |
| Publish receipt | A timestamp, null until the broker confirmed the publish. The reconciler of the dispatch epic (#24) selects `published_at is null`, so a partial index covers exactly that predicate. | none |
| Status machinery | Tables `job_status` (status, terminal) and `job_transition` (from, to, `row_only`), a `CHECK` on `accepted_job.status` and a `BEFORE UPDATE` trigger that refuses any move not in `job_transition`, all in one generated repeatable file read from `contracts/status/status.yaml` and the class vocabulary read from `contracts/job/limits.yaml`. A move that `row_only` marks is taken only when the caller says the job never started, which the library enforces and a trigger cannot. A late writer finds the row moved already and loses the compare-and-set. | 4 |
| Control columns | The requested action, a control sequence, a delivered time and a pending patch reference, as in the epic. `requested_action` stays set until the guarded `transition` that applies it clears it: the move into `paused` clears a pause, the move from `resume-pending` to `running` or the row-only move to `accepted` clears a resume, and a move into a terminal status clears whatever remains. `mark_delivered` sets `control_delivered_at` and never clears the action, so an action is outstanding exactly while `requested_action` is not null, delivered or not. `request_action` is a compare-and-set on the pair `(requested_action, control_seq)`: a repeat of the outstanding action is a no-op that returns the same sequence, and a different action takes the next one. The dispatch epic (#24) confirms the semantics in its own spec. | 3 |
| Table `event` | `event_id text` (`<source>#<id>`), `event_time timestamptz`, `type`, `source`, `tenant`, `job_id uuid`, `task_id` (null), `attempt integer` (null, no default) and `body text` holding the canonical bytes of `wdf_events.to_event_row` (never `jsonb`, which reorders keys). A hypertable on `event_time` with a one day chunk interval, `unique (event_id, event_time)`, compression after 2 days segmented by `job_id`, and the retention window of `retention.yaml`. The chunk interval is the one number that the open soak host decides, and one day is the default until it is chosen. | 5 |
| Batch insert | `insert_events(conn, events)` sends arrays through `unnest` with `on conflict (event_id, event_time) do nothing` and returns how many rows were new and how many were duplicates. The batch size is a constant that E.2 sets from the ingest table of S8, starting at 5,000 rows (an estimate). | 5 |
| Table `artifact` | One table, as the epic body decides (decision 18). `row_id bigint generated always as identity` (primary key), `kind text` (checked against the kinds of `contracts/artifact/layout.yaml` plus `compile-cache` by the generated `R__artifact_kinds.sql`, which also carries the producer-nullness check), `uri text unique` (null for a cache entry), `bucket`, `key`, `sha256`, `size`, `name`, `producer_job`, `producer_task` and `producer_attempt` (the attempt identity as the reference carries it, pattern `^r[0-9]+a[0-9]+$`, no default, all three null exactly for the content-addressed kinds and set for the attempt-scoped kinds, a rule that the generated `R__artifact_kinds.sql` holds from the addressing class of each kind in `layout.yaml`, while `0300` holds no kind list of either class and names only `compile-cache`), `created_at` and `deleted_at`, and the cache columns `cache_key` (64 hex), `wasm_uri text references artifact (uri)` with no cascade, `componentize_py_version`, `wit_version` and `last_used_at`. `CHECK` constraints make a row either a reference (`kind <> 'compile-cache'` and no list of kinds, because the kind lists of both addressing classes live only in the generated `R__artifact_kinds.sql`; `uri` and `sha256` are set, the cache columns are null) or a cache entry (`kind = 'compile-cache'`, the cache columns are set, `uri` is null, and all three producer columns are null, held by the cache branch of the `CHECK` in `0300`, while the producer check of the generated file covers only the layout kinds), and a partial unique index on `cache_key where kind = 'compile-cache'` makes the first writer win. A cache entry counts as a reference to its wasm: the foreign key refuses a raw delete of that row, and the library refuses to soft delete a wasm that a live entry names and refuses to point an entry at a wasm that is missing or soft deleted. The cache semantics are the artifact epic's (#12). | 6 |
| Retention windows | `contracts/state/retention.yaml` holds the numbers that question 1 selects, in two blocks. `windows` (the floor applies) holds the drop of events after 14 days, the results safety net of 28 days and the checkpoints safety net of 14 days. `housekeeping` (no floor) holds the compression of events after 2 days and the abort of incomplete multipart uploads after 1 day. Sources and components have no age rule. The loader rejects an entry of `windows` below twice `MAX_JOB_DURATION_DAYS`, rejects any entry for `accepted_job` or for the content-addressed buckets (`retention-on-job-rows`, `age-rule-on-content-addressed`), and the artifact epic (#12) sets its own windows below these. | open question |
| Migrations | Plain SQL in the package that owns the table, found through the entry point group `wdf.migrations`, so no central list is edited. Files are `<NNNN>_<name>.sql` (applied once, checksum recorded, a changed applied file is refused) and `R__<name>.sql` (repeatable, applied after every numbered file when its checksum changes). Number ranges are disjoint by construction: lane J `0100` to `0199`, lane E `0200` to `0299`, lane A `0300` to `0399`. The group roles of lane Q are one repeatable file, `R__roles.sql`. A statement that drops, renames or tightens (`DROP`, `RENAME`, `SET NOT NULL` without a default, a type change) is refused unless the file is named `*.contract.sql` and the run passes `--allow-contract`, so a release N+1 migration is always expand-only and release N keeps working. The lint scans numbered files only. Repeatable files are re-applied after a contract change and so must replace what they own: they may `DROP ... IF EXISTS` a constraint, trigger, function or policy that they create and then create it again (a constraint is re-added `NOT VALID` and then validated), and the lint refuses in them `DROP TABLE`, `DROP COLUMN`, `RENAME` and a type change. | 2 |
| Pre-deploy migration | The migration runs from the host as the `local_resource` of the helper module `deploy/appdb/migrate/` (module `appdb-migrate`, `depends_on: [infra/appdb]`), which waits for the app database and reaches it through a `kubectl port-forward` bound to 127.0.0.1, because no service image exists yet. The Tiltfile enables only resources named after a module (`config.set_enabled_resources` over the module names), so every helper step is a module of its own and never a second resource of another module. The first service epic that builds an image may move it to a Kubernetes Job without changing the runner. | 2 |
| Credentials | Generated on first use into the state directory of the cluster scripts (`~/.local/state/wasm-dag-forge`), delivered as Kubernetes Secrets, and never committed. The store contract loader rejects a file that holds a key named like a secret (`secret-in-contract`), and a test greps the tracked files of the new directories. | none |
| Object store roles | Nine identities in `contracts/store/roles.yaml` (`indexer` and `export` are decision 19): `admin` (bucket and lifecycle provisioning only, used by the bootstrap and by no pod), `submit` (writes `wdf-sources`), `indexer` (writes `wdf-sources` only, for the aggregate index objects that the epic body lists there), `compile` (reads `wdf-sources`, writes `wdf-components`), `simulate` (reads `wdf-components`, writes `wdf-results` and `wdf-checkpoints`, reads `wdf-checkpoints`), `aggregate` (reads `wdf-results`, writes `wdf-results`), `workflow` (reads `wdf-sources`, `wdf-components` and `wdf-results`), `export` (reads `wdf-results`, for the evidence export that the epic body lists as a reader) and `reaper` (lists and deletes in all four buckets, for the artifact epic). The role split stays at bucket level, and prefix-level restriction is not tested. | open question |
| Lifecycle numbers | The detection interval of the lifecycle job is 60 minutes, so a rule runs up to an hour late and a dead worker is a capacity alert and not a silent failure. Abort of incomplete multipart uploads after 1 day on every bucket, the hard expiries of `retention.yaml` on results and checkpoints, and no age rule on sources and components. | none |
| Volume sizing | `volumeSizeLimitMB = floor(0.8 x PVC MiB / (7 x 4))` with `-volume.max=0`, because the first write to a bucket grows up to 7 volumes and there are four buckets. The first write to each bucket is a check of the contract script. | none |
| Capacity | The capacity input of a bucket store is the used share of its volume claim, the alert input fires at 70 percent, well below the 90 percent that the epic names, because SeaweedFS holds back about 12 MiB and RustFS runs to zero. | none |
| Volume claims | Temporal's Postgres 5 GiB (3,507 MiB at 100,000 jobs per S3b, with a quarter margin), the app Postgres and the store from the S8 result. The default kind storage class keeps volumes on the host data directory [read: `docs/cluster-host.md`]. Whether it enforces a claim size is not documented in this repository, so D.1 records `df` of the data volume inside the pod, and the epic body's own statement that only a tmpfs was tested stands. | open question |
| TimescaleDB | The official image, pinned by digest at the newest `-pg16` tag on the day M.1 starts, telemetry off by setting, and the source-available license of its compression and policy features accepted for the PoC and stated in the module README. | none |
| fsync on the default storage class | `pg_test_fsync` runs in the pod on its volume claim and prints its figures. They are recorded in Closure, with no pass line, because the epic gives no threshold. | open question |
| Invariant and drift queries | SQL files under `contracts/state/invariants/` that each return the offending rows, so an empty result passes: a job in `running` whose workflow id is not among the open workflow ids (a job that never started has no workflow, so `accepted`, a dead lettered job and the row-only moves are never reported, and `running` is the one status that always has a workflow), an open workflow id whose row is terminal or missing, a row with a `job.published` event and no receipt or past `accepted` with no receipt, an accepted event whose `jobclass` differs from the `job_class` of its row, and a `task.result.ready` event that names an artifact with no row. The caller supplies the open workflow ids because this epic never reads Temporal, the two workflow comparisons are skipped with a `skipped: <name>` line when no list is given, and the others always run. | none |
| Inputs for the observability epic | `contracts/state/inputs.yaml` lists named inputs with a unit and a source: connections, database size, hypertable chunk count, event ingest lag (`now()` minus the newest `event_time`), drift row count, bytes per bucket and the capacity share. The metric names and the exporters belong to the observability epic (#13). | none |
| Footprint measurement | Issue M.1 is a spike, so its result file states pass or fail with numbers. Its pass line is in issue M.1. The batch bench has no timing guards: it asserts counts and idempotence, and the measured rates go in the PR and in Closure (decision 16). | 5 |

## 1. Goal and non-goals

Goal: durable, queryable state for the platform, and the bytes the platform moves, without a second source of job truth.
The app Postgres holds the accepted-jobs table, which is the dedup point and the control record of every job, and TimescaleDB holds the append-only event history.
Object storage holds manifests, source bundles, wasm, task outputs and checkpoints behind immutable references, in four buckets by data class.
Temporal owns workflow lifecycle, dependencies, retries and the task backlog, and nothing in this epic decides lifecycle.

Terms: the event history is the append-only event table of this epic, and the Temporal history is the workflow history inside Temporal, and the two are never used for each other.
The accepted-jobs table is `accepted_job`, and a row of it is the job row.

Non-goals for this pass:

- everything the epic lists under "Does not own": workflow lifecycle, retries, task state, the lease and the attempt identity (Temporal, #6, #10), admission and the meaning of the row (#8), the submit sequence, the reconciler and the control handlers (#24), the placement decision (#9), message schemas and event kinds (#4), the manifest content (#5), reference rules, integrity checks, failure handling, the retention policy and the reaper (#12), the checkpoint contract (#18), and dashboards and alerts beyond the inputs named here (#13)
- the services that write these tables, none of which exists yet, so every writer is tested through the library it will call and through stand-in roles, and the real use of the guarded transition, the batch insert and the grants belongs to #24, #8 and #6
- the real submit path of 100,000 jobs through the dispatch API (#24) and the Temporal visibility comparison for drift (the invariant queries take a list of open workflow ids from the caller)
- the `Days` lifecycle rule observed on objects that are actually a day old, which needs a 25 hour wait and belongs to the characterization soak (#16), while this epic ships the arming and the checking halves of that test
- a maximum job duration above 7 days, and every retention and sizing number for it (decision 7)
- disk behaviour on a real Kubernetes volume claim and a restart while full, because only a size-capped tmpfs was tested (the epic body) and whether the default storage class enforces a claim size is recorded by D.1
- replication, high availability, backups, and any managed or cloud database or store
- a verifier skill, feature files and verify recipes (decision 1)

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with `uv`, Python 3.13 and `pytest` from `uv.lock`, the recipes `just test`, `just itest [domain]` and `just ci`, and `scripts/lint.sh`, which runs `ruff`, `yamllint --strict` at 120 columns, `shellcheck`, `actionlint` and `kubeconform` over `deploy/` except `module.yaml`, `versions.yaml`, presets, `ports.yaml` and `deploy/tilt/` | `flake.lock`, `uv.lock`, recipe table v2 (17 names) | none, exists [read: `justfile`, `just/*.just`, `scripts/lint.sh`] |
| Dev environment | `deploy/versions.yaml` images map, whose entries are `{version, digest}`, with `postgres` 16.14 and `temporalio/temporal` 1.9.1 already pinned, and the policy in `docs/versions-policy.md`. This epic adds `timescale/timescaledb`, `chrislusf/seaweedfs` and `rustfs/rustfs`. | schema v1 | none, exists [read] |
| Cluster | Module contract: `deploy/<component>/module.yaml` and `Tiltfile.module`, `group`, `namespace`, `artifact.pin`, `resources`, `readiness`, `profiles`, `depends_on`, and the loader `contracts/module/check.sh` | `contracts/module/` module v1 | none, exists [read] |
| Cluster | Preset contract and the presets: the `ci` infra node is 3,072 MiB, the `dev` one 4,096 MiB and the `soak` one 6,144 MiB, the quota of `wdf-infra` is rendered from the preset by `scripts/cluster/namespaces.sh` [ran: 2,756 MiB for `ci` and 3,764 MiB for `dev`], and `scripts/cluster/slots.sh` reports every slot of `ci` as fitting [ran] | `contracts/preset/` preset v1, `deploy/presets/{ci,dev,soak}.yaml` | none, exists. R.1 changes the infra node of `ci` and `dev`. |
| Cluster | Topology: namespaces `wdf-infra`, `wdf-control`, `wdf-workers` and `wdf-obs`, the pool label `wdf.dev/pool`, and the rule that control pods share the infra pool | `contracts/topology/` topology v1 | none, exists [read] |
| Cluster | The cluster recipes and rules: `just up`, `just down` and `just nuke`, the lock `/tmp/wdf-cluster.lock` (taken by `just up`, `just down` and `just nuke` themselves unless `WDF_CLUSTER_LOCK_HELD` is set), `scripts/tilt/run.sh <preset>` (`tilt ci` for `ci` and `soak`), the persistence of volume claims on the host data directory of the preset, and the fact that `just up` does not apply the namespaces | `scripts/up.sh`, `scripts/tilt/run.sh`, `docs/cluster-host.md`, recipe table v2 | none, exists [read]. R.1 makes `just up` apply the namespaces. |
| Cluster | Spike S2 figures: the infra node of `ci` must fit Temporal, its Postgres and the object store at 2,314 MiB of requests and limits | `docs/spikes/S2-footprint.md` | numbers are inputs and are replaced by the S8 result |
| Envelope | Events contract: the CloudEvents profile, `time` with exactly six fractional digits, the `dataschema` identifier, and the key rule `<source>#<id>` plus `time` for the event history | `contracts/events/` events v3 [ran: `VERSION` is 3] | none, exists |
| Envelope | Event library: `wdf_events.to_event_row(event) -> (key, time, body)`, `to_bytes`, `from_bytes`, `build_event`, the models of `wdf_events.models` (`AcceptedData`, `ResultReadyData`, `ContentReference`, `AttemptReference`, `JobParams`) and `psycopg` as a dependency | `packages/events/` library v1 | none, exists [read] |
| Envelope | Status contract: ten statuses, the terminal set, the views, the projection of each lifecycle event type and the transition matrix with its `row_only` moves | `contracts/status/` status v1 | none, exists [read: `status.yaml`] |
| Envelope | Artifact contract: the reference shape, the four buckets, the ten kinds with their addressing class and key template, and the attempt identity pattern of `producer.attempt` | `contracts/artifact/` artifact v2 [ran: `VERSION` is 2] | none, exists |
| Envelope | Key derivation: `job_id`, `batch_id`, `workflow_id`, `attempt_identity`, `cloudevent_id` and the five-input `compile_cache_key` | `packages/keys/` keys v1, `contracts/events/vectors/keys.json` | none, exists [read] |
| Envelope | Compatibility window: `MAX_JOB_DURATION_DAYS` of 7 and `DEAD_LETTER_RETENTION_DAYS` of 14, and a previous major that stays readable 21 days | `packages/compat/` compat v1 | none, exists [read] |
| Envelope | Stage contract: which stage emits which event and writes what, so the writers of a table are known | `contracts/stages/` stages v1 | none, exists [read] |
| Envelope | Instrumentation rules for metric names and labels, which the inputs file of this epic follows without defining metrics | `contracts/instrumentation/` instrumentation v1 | none, exists [read] |
| Envelope | The T1 container helper: `wdf_itest.Session.container(image_key, ports, command, env)`, the `t1` fixture, the lock files, the 1 GiB cap and the run label `wdf.dev/verify-run`, which has no tmpfs, mount or exec option | `packages/itest/` | none, exists. R.2 adds the options that the store tests need. |
| Validator | The job contract: `max_job_duration_days` of 7, the `classes` of the limits profile, `wdf_plan.manifest_reference` and `plan_hash`, and the per-job parameter block that `check_job_params` validates before the row is written | `contracts/job/` job v2 [ran: `VERSION` is 2], `packages/plan/` plan v1 | none, exists |
| Spikes | Temporal's Postgres grows about 35 KiB per job with the visibility database at 71 percent, at 1 GiB memory and 4 GiB or more of disk for 100,000 jobs (S3b). The store behaviour of S5: the 14 base checks, the role matrix, expiry, the full disk, and its product facts. RabbitMQ peaked at 154.6 MiB (S4) | `docs/spikes/temporal.md`, `docs/spikes/object-store.md`, `docs/spikes/broker-client.md` | none |
| Spikes | The S5 harness (`s3_contract.py`, `roles.json`) that the epic names as the starting point | not in the repository [ran: `ls spikes` shows only `S2`] | issue C.1 rebuilds the script from the method of `docs/spikes/object-store.md`, and starts from the original files if the captain supplies them |
| Epic bodies of #12, #24, #6, #8, #13, #16 and #17 | What each expects of this epic: the index rows and the reference count (#12), the control columns, the fingerprint and rows written with their events in one transaction (#24), the guarded transition (#6, #8), the metric inputs (#13) and the invariant queries (#16, #17) | read 2026-11-01 | none, the Produces table below answers each row |

No `revisit` child issue is open against the dev environment, cluster, envelope or validator epics [ran: `gh-axi issue list --label revisit --state all` shows only two closed issues].
The two revisit children that this spec adds are R.1 (cluster) and R.2 (envelope).
The names below were checked free on `main` [ran: no `contracts/state/`, `contracts/store/`, `packages/migrate/`, `packages/jobstate/`, `packages/eventstore/`, `packages/artifactindex/`, `packages/storecheck/`, `packages/stateops/`, `deploy/appdb/`, `deploy/temporaldb/` or `deploy/objectstore/` exists].

### Produces (each pinned precisely)

Names, shapes and versions below are proposals that lane 0 and the table issues pin, and a change after the pin is a contract-change PR with a consumer list and a bump of the contract `VERSION`.
For a generated schema or file that PR carries the contract file, the model under `packages/` and the regenerated output together, because the drift check fails otherwise.
Epic text and notes link to these files and never copy them.

| To | What | Where it lives (path) | Shape (schema file, signature, exit codes, key names) | Version |
| --- | --- | --- | --- | --- |
| #24, #8, #6, #7, #12, #16, #17 | State contract: the column tables of the three table families, the row models' exported schemas, the retention windows, and the loader | `contracts/state/` | `README.md`, `VERSION`, `retention.yaml` with the generated `retention.schema.json`, `schemas/{job-row,event-row,artifact-row}/1.json`, `vectors/`, and later `inputs.yaml` with its generated `inputs.schema.json`, and `invariants/` (each added by its own issue). `check.sh <file>` validates `retention.yaml` only and exits 0 valid, 1 invalid with `reason: <code>: <detail>` on stderr, 2 usage. The inputs file has its own loader, `python -m wdf_stateops.inputs --check FILE`, with the reasons `schema-violation` and `unknown-source` and the same exit codes. Reasons: `schema-violation`, `window-below-twice-max-job-duration`, `retention-on-job-rows`, `age-rule-on-content-addressed`. | state v1 |
| every writer | Migration runner and the database test fixture | `packages/migrate/` (import `wdf_migrate`) | `python -m wdf_migrate apply\|status\|check --dsn DSN [--allow-contract]`, exit 0 ok, 1 refused (checksum, lint, a contract step without the flag), 2 usage, 3 database unreachable. Entry point group `wdf.migrations`. The pytest fixture `appdb` (module scoped container of the pinned TimescaleDB image, a fresh database per test with all migrations applied) is registered as a plugin by the package. | migrate v1 |
| #24, #8, #6, #7 | Job state library: `insert_accepted(conn, rows) -> InsertResult`, `get_job(conn, job_id)`, `transition(conn, job_id, expected, to, row_only=False) -> Moved \| Lost`, `request_action(conn, job_id, action) -> ControlRequest`, `mark_published(conn, job_id)`, `mark_delivered(conn, job_id, control_seq)`, `status_vocabulary()` | `packages/jobstate/` (import `wdf_jobstate`) | Every function takes an open connection and never commits, so the caller composes a row change and its events in one transaction. `InsertResult` lists the new job ids and, for each duplicate, the existing job id, status and fingerprint, which is what the dispatch epic needs for `KEY_CONFLICT`. `Lost` carries the status that won. A move not in `job_transition` raises `IllegalTransition`. `python -m wdf_jobstate.gen [--check]` writes and checks the generated migration and the exported schema. | jobstate v1 |
| #6, #24, #16 | Event store library: `insert_events(conn, events) -> BatchResult(inserted, duplicates)`, `events_for_job(conn, job_id)`, `chunk_count(conn)` | `packages/eventstore/` (import `wdf_eventstore`) | Takes `wdf_events.Event` objects and uses `to_event_row`, so the key and the byte-exact body are the envelope's. `python -m wdf_eventstore.gen [--check]` writes and checks the generated policy file and the exported schema. | eventstore v1 |
| #12, #10, #11, #6 | Artifact index library: `index_reference(conn, reference) -> Indexed \| Existing`, `mark_deleted(conn, uri, at) -> Deleted \| Pinned`, `references_to(conn, uri)`, `record_cache_entry(conn, entry) -> Won \| Lost \| WasmGone`, `touch_cache_entry(conn, key, at)` | `packages/artifactindex/` (import `wdf_artifactindex`) | A reference is checked against `wdf_events.models` before it is stored, and an attempt identity is stored as given and never computed. `Pinned` means that a live cache entry names the wasm and `WasmGone` that the wasm row is missing or soft deleted. Both checks and their write run in one statement with the wasm row locked `FOR SHARE`, so they cannot race. `python -m wdf_artifactindex.gen [--check]` writes and checks the generated `R__artifact_kinds.sql` (the kind check and the producer-nullness check) and the exported schema. | artifactindex v1 |
| #12, #15, #16, #10, #7 | Store contract: the roles with their actions per bucket, the lifecycle rules, the per-product known gaps, and the loader | `contracts/store/` | `roles.yaml`, `lifecycle.yaml`, `products.yaml`, the generated `store.schema.json`, `README.md`, `VERSION`, `vectors/`. `check.sh <file>` exits 0 valid, 1 with `reason:` (`schema-violation`, `unknown-bucket`, `role-outside-matrix`, `secret-in-contract`, `bucket-without-rules`), 2 usage. The bucket names are read from `contracts/artifact/layout.yaml` and never retyped. | store v1 |
| #12, #15, #16, CI | S3 contract script, runnable against any S3 endpoint | `packages/storecheck/` (import `wdf_storecheck`) | `python -m wdf_storecheck run --endpoint URL --credentials FILE --mode base\|access\|lifecycle\|days-arm\|days-check\|disk-full\|recover\|marker-put\|marker-get\|capacity`, exit 0 every check passed or is a recorded known gap, 1 a check failed, 2 usage. `python -m wdf_storecheck.gen [--check]`. | storecheck v1 |
| the cluster, #6, #12 | Modules | `deploy/appdb/`, `deploy/appdb/migrate/`, `deploy/temporaldb/`, `deploy/objectstore/` and `deploy/objectstore/bootstrap/`, each with one `module.yaml` and one `Tiltfile.module` (the Tiltfile finds manifests at depth 1 or 2 under `deploy/`) | Five modules in `wdf-infra`. Module `appdb` is the app Postgres with TimescaleDB, `temporaldb` is Temporal's plain Postgres with its two databases, and `objectstore` is the store, one StatefulSet whose pod holds three containers `store`, `admin` and `worker` (the lifecycle worker), so one module and one Tilt resource cover all three and the pod is Ready only when all three containers are. The `objectstore` module has one `resources` entry that carries the requests and limits of the whole pod, and the manifest splits them across the containers. The modules `appdb`, `temporaldb` and `objectstore` have pinned images, volume claims and figures from the S8 result. Modules `appdb-migrate` and `objectstore-bootstrap` are host-side helpers: each registers one `local_resource` named after the module, has `depends_on` the module it acts on, and declares a nominal `resources` entry because the contract requires one and nothing of it runs in the cluster. A helper that is not a module of its own is disabled under `tilt ci`. | module v1 |
| #16, #17, #15 | Invariant queries and the batch bench | `contracts/state/invariants/`, `packages/stateops/` (import `wdf_stateops`) | `python -m wdf_stateops.invariants --dsn DSN [--open-workflows FILE]` exits 0 clean, 1 with one `invariant: <name>: <key>` line per offending row, 2 usage, and without `--open-workflows` the two workflow comparisons print `skipped: <name>`. `python -m wdf_stateops.bench --jobs N [--dsn DSN] [--twice]` exits 0 when the counts are right, 1 when they are not, 2 usage, and prints `rows_inserted`, `events_inserted` and the seconds of each phase, and with `--twice` it runs the insert again in the same invocation and prints the counts of the second run. | stateops v1 |
| #13, #16 | Metric and alert inputs | `contracts/state/inputs.yaml`, `python -m wdf_stateops.inputs --dsn DSN [--store-endpoint URL --credentials FILE]` and `--check FILE` | Each input has a name, a unit, a source (a SQL query or a store source) and, where it alerts, a threshold. The command prints `<name> <value>` per input. | state v1 |
| #24, #8, #6 | Group roles and grants | `packages/stateops/`, the hand written repeatable migration `R__roles.sql` shipped as package data | `NOLOGIN` roles `wdf_dispatch`, `wdf_control`, `wdf_admission`, `wdf_workflow`, `wdf_artifacts`, `wdf_reaper` and `wdf_reader` with column grants per table, each role created only when absent and every grant applied again on every run, because roles are cluster wide and one container serves many databases. The writers table below is the statement and a catalog test holds the file to it. Login roles are the service epics'. | state v1 |
| the cluster, every module owner | Resized infra node and applied namespaces | `deploy/presets/{ci,dev}.yaml`, `scripts/up.sh` | The infra node figures of the S8 result, and `just up` that applies `scripts/cluster/namespaces.sh apply <preset>` and stays idempotent. | preset v1 |

### Writers, one per kind of change

The table below is the writers statement of the epic made concrete.
The libraries above are the only code that issues these statements, and the grants of Q.4 enforce it at table and column level.
A status value cannot be restricted by a grant, so the trigger and the library keep that rule.

| Change | Writer | Statement |
| --- | --- | --- |
| Insert the row and its accepted event, then set the receipt | the submit sequence of the dispatch API (#24), role `wdf_dispatch` | `insert_accepted`, `insert_events`, `mark_published` |
| Request cancel, pause, resume or terminate, and a row-only move of a job that never started | the control API (#24), role `wdf_control` | `request_action`, `mark_delivered`, `transition(..., row_only=True)` and their events |
| Claim a job | admission (#8), role `wdf_admission` | `transition(accepted, admitted)` and `insert_events` for the admitted event |
| Project running, paused and the terminal statuses | the workflow (#6), role `wdf_workflow` | `transition` and `insert_events` for every event except submit, control and admission events |
| Index a committed reference and a cache entry | the artifact client (#12), role `wdf_artifacts` | `index_reference`, `record_cache_entry`, `touch_cache_entry` |
| Soft delete an expired reference | the reaper (#12), role `wdf_reaper` | `mark_deleted` |

## 3. Lanes (disjoint file ownership)

Package names are proposals.
Lane 0 owns every package it creates until the owning lane takes over the glob, as the method says for stubs.

Departures from the method, stated so that the captain approves them knowingly:

- Lane Needs run across lanes: C.1 waits for R.2, O.1 for C.1 (its acceptance runs the contract script) and C.2 for O.1, Q.1 for J.2, E.2 and A.1, D.1 for J.1, Q.2 for D.1, Q.4 for J.1, E.1 and A.1, Q.3 for A.1, Q.1, O.1 and C.2, and X.1 for D.1, D.2, O.1 and C.1.
  The reason is that a test over a stub would only assert `NotImplementedError`, and the store module is accepted by the script that tests it.
- Lane 0 pins the contracts as row models, exported schemas, retention windows and the store roles, while the DDL belongs to lanes J, E and A.
  The method's tranche for this epic names the DDL as pinned in lane 0, and here a conformance test in each package, which accepts a missing table only for the names in its `STUBBED` list, holds the DDL to the pinned models, and the owning issue empties the list.
- Issue R.1 is a `revisit` child of the closed cluster epic (#2) and issue R.2 a `revisit` child of the closed envelope epic (#4).
  Each edits files of its epic and adds one dated closure addendum line to that epic's spec, as the method says.
  R.1 blocks the module lanes and R.2 blocks C.1.
- The root `pyproject.toml` is edited once, by lane 0 (issue 0.1), to list the new workspace members in its dependency group and its sources, because a plain `uv run` installs only what the dev group names.
- A contract-change PR for a generated schema is not `contracts/` only, because the model and the regenerated file must change with it.
- No `just/<domain>.just` module and no recipe is added (decision 3).
- The verifier delta section of the template is absent because there is none (decision 1), so the sections after section 5 are numbered one lower than in the template.
- `docs/method.md` is not edited by this spec.
  A follow-up edit is needed, because its verifier table (line 221) and the E5 row (line 247) still list `verify-storage`, and line 112 points the exit test at "spec section 7", which is section 6 here.
  The envelope and validator specs record the same follow-up.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| M Measure | 1 | `spikes/S8/**`, `docs/spikes/S8-state-footprint.md`, the S8 row of `docs/spikes/README.md` | M.1 | nothing (first) |
| R Revisit | 1 | R.1: `deploy/presets/{ci,dev,soak}.yaml`, `scripts/up.sh`, `docs/cluster-host.md`, one closure addendum line of `docs/epics/02-cluster.md`. R.2: `packages/itest/**`, one closure addendum line of `docs/epics/03-envelope.md` | R.1, R.2 | M.1 for R.1, nothing for R.2 |
| 0 Contracts and skeleton | 1 | `contracts/state/{README.md,VERSION,check.sh,retention.yaml,retention.schema.json}`, `contracts/state/schemas/**`, `contracts/state/vectors/retention/**`, `contracts/store/**`, the three new keys of `deploy/versions.yaml`, `packages/migrate/**`, the root `pyproject.toml` edit and `uv.lock`, and until lanes J, E, A, C and Q take over `packages/jobstate/**`, `packages/eventstore/**`, `packages/artifactindex/**`, `packages/storecheck/**` and `packages/stateops/**` | 0.1, 0.2, 0.3 | M.1 for 0.3 (barrier, merged alone) |
| J Jobs | 1 | `packages/jobstate/**`, `contracts/state/vectors/jobs/**` | J.1, J.2 | lane 0 |
| E Events | 1 | `packages/eventstore/**`, `contracts/state/vectors/events/**` | E.1, E.2, E.3 | lane 0 |
| A Artifact index | 1 | `packages/artifactindex/**`, `contracts/state/vectors/artifacts/**` | A.1 | lane 0 |
| D Databases | 1 | `deploy/appdb/**`, `deploy/temporaldb/**`, `scripts/storage/db/**` | D.1, D.2 | lane 0 and R.1, and J.1 for D.1 |
| O Object store | 1 | `deploy/objectstore/**`, `scripts/storage/store/**` | O.1 | lane 0, R.1 and C.1 |
| C Store contract tests | 1 | `packages/storecheck/**` | C.1, C.2 | lane 0 and R.2 for C.1, C.1 and O.1 for C.2 |
| Q Cross-table | 1 | `packages/stateops/**`, `contracts/state/{inputs.yaml,inputs.schema.json}`, `contracts/state/invariants/**`, `contracts/state/vectors/{inputs,invariants}/**` | Q.1, Q.2, Q.3, Q.4 | lane 0, and the issues named in the departures above |
| X CI | 1 | `.github/workflows/state-modules.yml` | X.1 | D.1, D.2, O.1 and C.1 |

Shared-file rules:

- `uv.lock` is the one hotspot, and a shared file that every lane may touch, which the Done rule of section 4 allows.
  Lane 0 commits the `uv lock` result for the new workspace members, because `uv run --frozen` refuses an unlocked member.
  A dependency lands in the PR that needs it (`boto3` in C.1, and nothing else is expected: `psycopg`, `pydantic`, `jsonschema`, `pyyaml` and `hypothesis` are locked).
  A lock conflict is never resolved by hand, `uv lock` is rerun after rebase.
- `deploy/versions.yaml` is shared with every epic, one key per line, and a PR touches only its own keys.
  Lane 0 adds `timescale/timescaledb` (0.3), `chrislusf/seaweedfs` and `rustfs/rustfs` (0.2), and no other lane edits the file.
- `docs/spikes/README.md` gets one row from M.1 and is otherwise not touched.
- `contracts/state/README.md` is lane 0's, and a lane that adds a file to the contract adds its own table row in the PR that adds the file, which is the one allowed edit.
- `justfile`, `just/*.just`, `docs/dev-setup.md`, `deploy/host/ports.yaml` and `.github/workflows/*` are not touched, except that X.1 adds one new workflow file.
  Host access to the databases and the store goes through `kubectl port-forward` bound to 127.0.0.1, so no ports row is added and the cluster is not recreated.
- After lane 0 merges, a file of a pinned contract changes only through a contract-change PR, except the vectors that a lane adds under its own glob and the files that lane Q adds to `contracts/state/`.
- Migration numbers are disjoint by construction (section 0.3), and repeatable files are named `R__<name>.sql` inside the package that owns them, so two lanes never create the same file.
- Generated files (`contracts/state/schemas/**`, `contracts/state/inputs.schema.json`, `contracts/store/store.schema.json`, `R__job_vocab.sql`, `R__event_policies.sql` and `R__artifact_kinds.sql`) are committed, the `gen --check` commands regenerate them, and a lane never hand edits one.
- The contract YAML files sit under `contracts/`, so `scripts/lint.sh` applies `yamllint --strict` to them and does not apply `kubeconform`.
  The non-manifest YAML under `deploy/` is limited to `module.yaml`, which the lint already excludes, and the store identities file is JSON.
- No tracked file holds a credential.

## 4. Issues

Each block below is in the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md), and is replaced by a `#NNN` link after filing.
Issues are filed as sub-issues of #3, and R.1 and R.2 as `revisit` sub-issues of #2 and #4.
Each issue copies its Owns line from the lane table, uses its Test line as the Verifier section, and keeps only the `just ci` and Owns checkboxes of Done means.
Done means, for every issue: `just ci` is green on the branch (it runs `just itest`, so every lane's gate needs Docker as the envelope spec says), the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files of section 3 changed.
Acceptance lines are commands with an observable result, except the lines that say a result is pasted in the PR or in Closure, which the reviewer checks in the PR.
Commands are written without the `nix develop -c uv run --frozen` prefix that every Python command takes, and T2 commands run under `flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 ...` as the cluster epic says, because `just up`, `just down` and `just nuke` take the lock themselves unless that variable is set.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 App Postgres with TimescaleDB and Temporal Postgres | D.1, D.2, after M.1 and R.1 |
| 2 Migration tooling | 0.3 |
| 3 Accepted-jobs table | 0.1 (the row model and its schema), J.1 (the table) |
| 4 Status transition library | J.2 |
| 5 Event hypertable and idempotent batch ingest | E.1, E.2, E.3 |
| 6 Artifact index table and writer role | A.1, and the grants of Q.4 |
| 7 Deploy object storage | O.1, with the contract of 0.2 |
| 8 S3 contract test script | C.1, C.2, with the contract of 0.2 |
| 9 Invariant and drift queries | Q.1 |
| 10 Per-service database roles and grants | Q.4 (question 3) |
| 11 Metric and alert inputs | Q.3 |
| not in the epic's list: the footprint measurement and the preset resize that the foundation review asks for | M.1, R.1 |
| not in the epic's list: the container helper options, the namespaces in `just up`, the batch bench, the CI workflow | R.2, R.1, Q.2, X.1 |

### Consumes and produces per issue

The template's Consumes / produces section of each filed issue is its row below, with the pinned rows of section 2 and the stub names of section 5.

| Issue | Consumes | Produces |
| --- | --- | --- |
| M.1 | preset contract, spikes S2, S3b, S4 and S5 | the S8 result: node and quota table, PVC figures, image digests |
| R.1 | the S8 result, preset contract, cluster recipes | resized `ci` and `dev` presets, `just up` applying the namespaces |
| R.2 | `wdf_itest` | the tmpfs, mount and exec options |
| 0.1 | status v1, artifact v2, events v3, job v2, compat v1, keys v1 | `contracts/state/` (retention, row schemas), the six package skeletons and the workspace edit |
| 0.2 | artifact v2 layout, the images map, spike S5 | `contracts/store/`, the two store image pins |
| 0.3 | `wdf_itest`, the images map, the S8 result | `wdf_migrate`, the `appdb` fixture, the TimescaleDB pin, the credentials test |
| J.1 | status v1, job v2 limits, the row models of 0.1 | `0100_accepted_job.sql`, `R__job_vocab.sql` |
| J.2 | J.1 | `wdf_jobstate` functions |
| E.1 | events v3, `retention.yaml` | `0200_event.sql`, `R__event_policies.sql` |
| E.2 | `wdf_events.to_event_row`, the golden events | `wdf_eventstore.insert_events`, `events_for_job` |
| E.3 | E.1, E.2 | the compressed chunk and retention behaviour, `chunk_count` |
| A.1 | artifact v2, `wdf_events.models`, keys v1 | `0300_artifact_index.sql`, `R__artifact_kinds.sql`, `wdf_artifactindex` |
| D.1 | module v1, presets, the S8 result, `wdf_migrate`, J.1 | modules `appdb` and `appdb-migrate`, the fsync probe |
| D.2 | module v1, the `postgres` pin | module `temporaldb` |
| O.1 | store contract, module v1, spike S5, C.1 | modules `objectstore` and `objectstore-bootstrap`, the runbook |
| C.1 | store contract, the R.2 options, spike S5 | `wdf_storecheck` base and access modes |
| C.2 | C.1, O.1 | the lifecycle, days, disk-full, recover, marker and capacity modes |
| Q.1 | J.2, E.2, A.1 | `contracts/state/invariants/`, `wdf_stateops.invariants` |
| Q.2 | Q.1, D.1 | `wdf_stateops.bench` |
| Q.3 | J.1, E.1, A.1, Q.1, O.1, C.2 | `contracts/state/inputs.yaml`, `wdf_stateops.inputs` |
| Q.4 | J.1, E.1, A.1 | `R__roles.sql` |
| X.1 | D.1, D.2, O.1, C.1 | `.github/workflows/state-modules.yml` |

### M.1 Footprint of the real state components (spike S8)

Lane M, tier T1.
Needs: nothing.
Blocks: R.1, 0.3, D.1, D.2 and O.1.

Scope: measure the real components that this epic adds to the infra node, with the pinned images in containers capped as the earlier spikes did, and record the numbers that size the node.
The standard load is 100,000 jobs of two tasks, so 100,000 accepted rows in chunks of 1,000 and, at the epic's estimate of 6 events per job plus 4 per task, 14 event rows per job.

1. The app Postgres with TimescaleDB: peak working set under the row load, the event load and a forced compression of the old chunks, bytes on disk per job with and without compression, insert and ingest rates per batch size (1,000, 5,000 and 20,000 rows), container start time and image size.
2. The object store with `weed admin` and the lifecycle worker beside it: all four buckets provisioned so the volumes grow, then the S5 contract run, then 100,000 objects of 1 KiB, with peak working set and the number of volumes.
3. Temporal's Postgres at its S3b figure and RabbitMQ at its S4 figure of 154.6 MiB, carried and not re-run, and the `wdf-control` pods that share the infra node as a reserved line marked as an estimate until #24, #6 and #8 replace it.
4. The proposed infra node figure and the `wdf-infra` quota for `ci` and `dev`, with the arithmetic shown.

Pass line, binary: the result file holds the measured peaks of items 1 and 2, and a proposed infra node memory for `ci` and `dev` such that the sum of the component limits is at most the `wdf-infra` quota that `scripts/cluster/namespaces.sh render` gives for that size, the sum of the measured peaks plus 20 percent (a proposed headroom) is at most the node allocatable after the 50 MiB of daemons, and the `ci` sum of node limits stays at most 15,984 MiB, the 16,367,524 kB `MemTotal` of the runner that the `kind-smoke` run of the cluster closure recorded.
Anything else is fail, and the file names the smallest numbers that would pass or says that the figure cannot be met on that runner.

Owns: `spikes/S8/**`, `docs/spikes/S8-state-footprint.md`, the S8 row of `docs/spikes/README.md`.
Consumes: `docs/spikes/*.md`, `deploy/presets/`, `scripts/cluster/namespaces.sh render`.
Produces: the result file, with the digest of each image used, the TimescaleDB tag, the ingest table, the PVC figures that D.1 and O.1 use, and the infra node table that R.1 applies.

Acceptance criteria:

- [ ] `spikes/S8/run.sh` exits 0 and prints one `figure <name> <value>` line for every number of the result file, so the file can be checked against a run.
- [ ] `docs/spikes/S8-state-footprint.md` states pass or fail, holds the environment, the measured numbers, the proposed node and quota table for `ci` and `dev`, the digest of every image, and the ingest rate per batch size.
- [ ] The result lists container start time and image size of each image, which the cost section of this spec reads.
- [ ] Containers used the worker slot ports on 127.0.0.1 and the run label, and `docker ps -a --filter label=wdf.dev/verify-run` lists none after the run.

Test: the script above, no pytest, because throwaway spike code is outside the test suite as the method says.
Done means: the result is merged before R.1 and 0.3 are filed.

### R.1 Resize the infra node and apply the namespaces in `just up` (revisit of the cluster epic)

Lane R, tier T0 and T2.
Needs: M.1.
Blocks: D.1, D.2, O.1.
A `revisit` child of #2.
Decisions 9 and 10 of section 0.1 apply.

Scope: apply the infra node figures of the S8 result to the `ci` and `dev` presets (and `soak` only if the table says its infra node changes), state in `docs/cluster-host.md` that a preset resize needs `just nuke` first on a host that holds a cluster (the kubelet reservation and eviction thresholds are written at node creation while a container limit can be raised afterwards, so a live node ends in the `MISMATCH` of `scripts/cluster/allocatable.sh`), have `scripts/up.sh` say so when it finds that mismatch on an existing cluster instead of only exiting 1, and make `scripts/up.sh` call `scripts/cluster/namespaces.sh apply "$preset"` after the nodes are Ready, so the quota that the modules are sized against is in force.
The recipe stays idempotent.

Owns: `deploy/presets/{ci,dev,soak}.yaml`, `scripts/up.sh`, `docs/cluster-host.md`, one dated closure addendum line of `docs/epics/02-cluster.md`.

Acceptance criteria:

- [ ] `contracts/preset/check.sh deploy/presets/ci.yaml`, then the same for `dev` and `soak`, exit 0, so the sums of node limits still agree.
- [ ] `for p in ci dev soak; do scripts/cluster/slots.sh $p; done` exits 0 with every slot reported as fitting.
- [ ] `scripts/cluster/namespaces.sh render ci` prints a `wdf-infra` quota whose `limits.memory` equals the figure of the S8 table for `ci`, and the same for `dev`.
- [ ] T2: from no cluster, `just up` exits 0, then `kubectl --context kind-wdf get ns wdf-infra wdf-control wdf-workers wdf-obs` lists all four, and `kubectl --context kind-wdf -n wdf-infra get resourcequota pool-capacity -o jsonpath='{.spec.hard.limits\.memory}'` equals the S8 figure.
- [ ] T2: a second `just up` exits 0 and leaves every node container id and creation time unchanged.
- [ ] T2: on a host that holds a `ci` cluster created from the presets of `dfaa8f4`, `just up` after the resize exits 1 with a line that names `just nuke`, and after `just nuke` and `just up` the node limits and the quota are the new figures.
- [ ] `just doctor` exits 0 with `memory-vs-preset` reading `ok`, on a host with at least the new `ci` sum.

Test: the commands above, and `just ci`, which fails if a figure test of `scripts/cluster/test_*.py` still holds an old number, fixed under this issue.

### R.2 Container helper options (revisit of the envelope epic)

Lane R, tier T1.
Needs: nothing.
Blocks: C.1.
A `revisit` child of #4.

Scope: add three options to `wdf_itest.Session.container`, all off by default so no existing test changes: a size-capped `tmpfs` mount, read-only `mounts` of a file or directory (for the store identities file), and an `exec` call that runs a command in the labelled container (for the vacuum procedure).
The one-container-at-a-time rule, the two lock files and the 1 GiB cap are unchanged.

Owns: `packages/itest/**`, one dated closure addendum line of `docs/epics/03-envelope.md`.

Acceptance criteria:

- [ ] `pytest packages/itest -q -rs` passes with no `SKIPPED` line, including `test_tmpfs_is_size_capped`, `test_mount_is_read_only` and `test_exec_runs_in_the_labelled_container`.
- [ ] `pytest packages/events packages/temporal -m integration -q -rs` still passes, so the existing T1 tests are untouched.
- [ ] `docker ps -a --filter label=wdf.dev/verify-run` lists no container after the run.

Test: the three tests above.

### 0.1 State contract, row models and retention

Lane 0, tier T0.
Needs: nothing.
Decisions 6, 7, 8 and 11 of section 0.1 apply, and question 1 of section 0.2.

Scope: create `contracts/state/` with its `README.md` (the column tables of section 0.3, the writers table of section 2 and the retention rules), `VERSION`, `retention.yaml` with the numbers question 1 selects, the generated `retention.schema.json`, the loader `check.sh` with its reasons and vectors, and the loader test.
Create all six packages of this epic (`migrate`, `jobstate`, `eventstore`, `artifactindex`, `storecheck` and `stateops`) as skeletons, so that the one edit of the workspace lists every member. `migrate`, `storecheck` and `stateops` start as importable packages with a stub module, a `tests/` directory and a wheel test, and their issues fill them. For `jobstate`, `eventstore` and `artifactindex` the skeleton is: the row model of each table (`JobRow`, `EventRow`, `ArtifactRow`) with its `gen --check`, the `contract/` links that carry the status table, the limits file, the artifact layout and the schemas as package data, a wheel test that builds the wheel and uses it from a clean virtual environment, and stubs that raise `NotImplementedError("not implemented: owned by lane J")` and the same for E and A.
Add all six members to the root `pyproject.toml` (the dependency group and the sources) and commit the `uv lock` result.
A model change after this issue merges is a contract-change PR.

Owns: `contracts/state/{README.md,VERSION,check.sh,retention.yaml,retention.schema.json}`, `contracts/state/schemas/**`, `contracts/state/vectors/retention/**`, the root `pyproject.toml`, `uv.lock`, and `packages/jobstate/**`, `packages/eventstore/**`, `packages/artifactindex/**`, `packages/storecheck/**` and `packages/stateops/**` until their lanes take over, and `packages/migrate/**` (which 0.3 fills).

Acceptance criteria:

- [ ] `python -m wdf_jobstate.gen --check`, `python -m wdf_eventstore.gen --check` and `python -m wdf_artifactindex.gen --check` exit 0.
- [ ] After renaming a field of `JobRow`, `python -m wdf_jobstate.gen --check` exits 1 naming `contracts/state/schemas/job-row/1.json`.
- [ ] `contracts/state/check.sh contracts/state/retention.yaml` exits 0, and every file under `contracts/state/vectors/retention/invalid/<reason>/` exits 1 with that `reason:`, covering `window-below-twice-max-job-duration`, `retention-on-job-rows` and `age-rule-on-content-addressed`.
- [ ] `pytest packages/jobstate packages/eventstore packages/artifactindex -m "not integration" -q -rs` passes with no `SKIPPED` line, including the retention rule test that reads `MAX_JOB_DURATION_DAYS` from `wdf_compat.window`.
- [ ] `pytest packages/jobstate packages/eventstore packages/artifactindex -q -k wheel` passes, so each wheel works from a clean virtual environment away from the repository.
- [ ] `uv lock --check` exits 0, and `uv run --frozen python -c 'import wdf_migrate, wdf_storecheck, wdf_stateops'` exits 0.
- [ ] `pytest packages/jobstate packages/eventstore packages/artifactindex -q -k readme` passes, including `test_readme_lists_every_model_field` in each package (every field of `JobRow`, `EventRow` and `ArtifactRow` appears in the column tables of `contracts/state/README.md`) and `test_readme_states_the_attempt_rule` (the README carries the sentence that the store never infers an attempt).

Test: `packages/*/tests/test_gen.py`, `test_wheel.py`, `test_vectors.py`, `test_readme.py` and `contracts/state/vectors/retention/`.

### 0.2 Store contract and object store image pins

Lane 0, tier T0.
Needs: 0.1.
Decisions 7 and 15 and questions 1 and 4 apply.

Scope: create `contracts/store/` with `roles.yaml` (the nine identities of section 0.3 and their actions per bucket), `lifecycle.yaml` (the rule kinds per bucket, with the window values read from `retention.yaml` by key), `products.yaml` (the known gaps per product, with `lifecycle-edit-by-writer` for SeaweedFS under question 4), the generated `store.schema.json`, the loader with its reasons and vectors, `README.md` and `VERSION`.
The bucket names come from `contracts/artifact/layout.yaml`, and the loader fails when a bucket is missing or extra.
Fill the skeleton of `storecheck` that 0.1 created with its models and `gen --check`.
Add `chrislusf/seaweedfs` 4.51 and `rustfs/rustfs` 1.0.4 to `deploy/versions.yaml` at the digests recorded in `docs/spikes/object-store.md`.

Owns: `contracts/store/**`, the two keys of `deploy/versions.yaml`, and `packages/storecheck/**` until lane C takes over.

Acceptance criteria:

- [ ] `contracts/store/check.sh contracts/store/roles.yaml`, `lifecycle.yaml` and `products.yaml` exit 0, and every file under `contracts/store/vectors/invalid/<reason>/` exits 1 with that `reason:`, covering `unknown-bucket`, `role-outside-matrix`, `secret-in-contract` and `bucket-without-rules`.
- [ ] `python -m wdf_storecheck.gen --check` exits 0, and after renaming a model field it exits 1 naming `contracts/store/store.schema.json`.
- [ ] `scripts/versions.sh --check` exits 0 with both new keys, and the digests equal those of `docs/spikes/object-store.md`.
- [ ] `pytest packages/storecheck -m "not integration" -q -rs` passes with no `SKIPPED` line, including a test that every bucket of `layout.yaml` has a rule and that every grant of `roles.yaml` names a bucket of `layout.yaml` and an action of the vocabulary of its schema.
- [ ] `pytest packages/storecheck -q -k wheel` passes.

Test: `packages/storecheck/tests/test_gen.py`, `test_roles.py`, `test_wheel.py`, and the vectors.

### 0.3 Migration runner, app database fixture and TimescaleDB pin

Lane 0, tier T1.
Needs: 0.1 and M.1.
Question 2 applies (default (a)).

Scope: the package `migrate` with the runner of section 0.3: discovery through the `wdf.migrations` entry point group, numbered and repeatable files, a `schema_migration` table with checksums, an advisory lock, one transaction per file, the expand-only lint and the `*.contract.sql` rule, and the commands `apply`, `status` and `check`.
A pytest fixture `appdb`, registered as a plugin: a module scoped container of the pinned TimescaleDB image through `wdf_itest`, with a fresh database per test and all migrations applied.
Add `timescale/timescaledb` to `deploy/versions.yaml` at the tag and digest that M.1 recorded.
A fixture package under the tests of `migrate` stands in for the table packages.
The package also holds `test_no_credentials_in_tracked_files`, which lists files with `git ls-files` inside a work tree and by walking the directories on disk in an export (the fallback of `scripts/lint.sh`) under `deploy/appdb`, `deploy/temporaldb`, `deploy/objectstore`, `scripts/storage`, `contracts/state` and `contracts/store` and refuses a key named like a secret or an access key pattern, and which passes while those paths are empty.

Owns: `packages/migrate/**`, the key `timescale/timescaledb` of `deploy/versions.yaml`.

Acceptance criteria:

- [ ] `pytest packages/migrate -q -rs` passes with no `SKIPPED` line, including `test_from_empty_and_from_previous_release_reach_the_same_schema` (the numbered files up to the last but one, then all, give the same introspected schema as all at once), `test_applying_twice_is_a_noop`, `test_changed_applied_file_is_refused`, `test_two_runners_serialize_on_the_lock`, `test_destructive_statement_needs_the_contract_suffix`, `test_repeatable_file_reapplies_when_its_checksum_changes`, `test_repeatable_file_may_replace_its_own_constraint` and `test_repeatable_file_refuses_dropping_a_table`.
- [ ] `python -m wdf_migrate check --dsn DSN` exits 1 for a database whose applied file was edited, and 3 for an unreachable database.
- [ ] `scripts/versions.sh --check` exits 0 with the new key.
- [ ] `pytest packages/migrate -q -k wheel` passes, and the wheel finds the migrations of an installed fixture package through the entry point.
- [ ] `pytest packages/migrate -q -k no_credentials_in_tracked_files` passes, and a test that adds `secretKey: x` to a scratch tracked file under `deploy/appdb/` makes it fail, and it passes from a `git archive` export outside any repository.
- [ ] `docker ps -a --filter label=wdf.dev/verify-run` lists no container after the run.

Test: `packages/migrate/tests/`.

### J.1 Accepted-jobs table

Lane J, tier T1.
Needs: lane 0.
Decisions 5 and 8 apply.

Scope: the table `accepted_job` of section 0.3 as migration `0100_accepted_job.sql` and a generated repeatable file `R__job_vocab.sql` (named in the package) holding `job_status`, `job_transition`, the `CHECK` on `status` and the class checks, read from `contracts/status/status.yaml` and `contracts/job/limits.yaml` by `python -m wdf_jobstate.gen`, with the `BEFORE UPDATE` trigger that refuses a move not in `job_transition`.
Indexes: `(tenant, batch_id, status)`, a partial index on `published_at is null`, and the manifest hash.
`jobstate` registers its migrations through the entry point.
The table conformance test empties its `STUBBED` list.

Owns: `packages/jobstate/**`, `contracts/state/vectors/jobs/**`.

Acceptance criteria:

- [ ] `python -m wdf_jobstate.gen --check` exits 0, and after adding an eleventh entry to `statuses` of `contracts/status/status.yaml` in a scratch copy it exits 1 naming `R__job_vocab.sql`.
- [ ] `pytest packages/jobstate -q -rs` passes with no `SKIPPED` line, including `test_status_set_is_the_ten_of_the_contract` (`job_status` holds exactly the ten statuses of the contract, a row with `partial` is accepted, rows with `queued` and with `pausing` are refused by the constraint), `test_job_class_default_is_short_and_checked_against_limits`, `test_unique_tenant_submission_key_is_the_one_dedup_point`, `test_parameters_must_be_an_object`, `test_seed_is_below_two_to_the_53`, `test_ddl_conformance` and `test_trigger_refuses_a_move_outside_the_table`.
- [ ] `test_transition_table_equals_the_contract` compares every `(from, to, row_only)` of `job_transition` with `status.yaml` and finds the same set.
- [ ] No column of `accepted_job` has a default other than the declared ones (`status`, `job_class`, `control_seq`, timestamps), asserted from `information_schema`.

Test: `packages/jobstate/tests/`.

### J.2 Guarded status transition library

Lane J, tier T1.
Needs: J.1.

Scope: `insert_accepted`, `get_job`, `transition`, `request_action`, `mark_published`, `mark_delivered` and `status_vocabulary`, as in section 2, each taking an open connection and never committing.
`transition` is a compare-and-set on the expected status with the guard of the transition table, sets `terminal_at` on a move into a terminal status, clears the requested action that the move applies (section 0.3) and sets `updated_at` always, and returns `Lost` with the winning status instead of raising when the row moved first.
A dead letter replay on a terminal row is therefore a no-op.

Owns: `packages/jobstate/**`.

Acceptance criteria:

- [ ] `pytest packages/jobstate -q -rs` passes with no `SKIPPED` line, including `test_late_writer_loses` (two connections attempt different moves of one row at once and exactly one wins, and the loser reads the winning status), `test_nothing_leaves_a_terminal_status`, `test_every_contract_transition_is_accepted_and_every_other_refused` (all 100 pairs of the ten statuses against the table), `test_row_only_moves_need_the_flag`, `test_terminal_at_is_set_on_the_terminal_move`, `test_request_action_repeat_is_a_noop_and_a_new_action_takes_the_next_sequence`, `test_repeat_after_delivery_is_still_a_noop` (pause, deliver, pause returns the first sequence), `test_the_move_into_paused_clears_the_requested_pause`, `test_a_terminal_move_clears_the_requested_action` and `test_insert_accepted_reports_the_fingerprint_of_a_duplicate`.
- [ ] A move on a terminal row returns `Lost` and does not raise, shown by `test_replayed_message_is_a_noop_for_a_terminal_row`.
- [ ] `python -c "import wdf_jobstate"` from a clean virtual environment built from the wheel works, as `test_wheel` shows.

Test: `packages/jobstate/tests/`.

### E.1 Event hypertable and policies

Lane E, tier T1.
Needs: lane 0.
Decision 6 and question 1 apply.

Scope: migration `0200_event.sql` creating `event` as in section 0.3 and turning it into a hypertable on `event_time` with the chunk interval of one day and compression settings, and a generated repeatable `R__event_policies.sql` that registers the compression and retention policies with the intervals of `contracts/state/retention.yaml`, written by `python -m wdf_eventstore.gen`.
The table conformance test empties its `STUBBED` list.

Owns: `packages/eventstore/**`, `contracts/state/vectors/events/**`.

Acceptance criteria:

- [ ] `python -m wdf_eventstore.gen --check` exits 0, and after changing a window of `retention.yaml` in a scratch copy it exits 1 naming `R__event_policies.sql`.
- [ ] `pytest packages/eventstore -q -rs` passes with no `SKIPPED` line, including `test_event_is_a_hypertable_on_event_time`, `test_unique_key_is_event_id_and_event_time`, `test_policies_carry_the_intervals_of_retention_yaml` (read from `timescaledb_information.jobs`), `test_attempt_columns_have_no_default` and `test_ddl_conformance`.
- [ ] `test_body_column_is_text_and_keeps_key_order` reproduces the envelope test on the real table, so a `jsonb` body would fail it.

Test: `packages/eventstore/tests/`.

### E.2 Idempotent batch insert

Lane E, tier T1.
Needs: E.1.

Scope: `insert_events` and `events_for_job`, using `wdf_events.to_event_row`, with the batching of section 0.3.
The key is the envelope's, and a different `time` for the same id is a different row, which is the hazard the epic names.

Owns: `packages/eventstore/**`.

Acceptance criteria:

- [ ] `pytest packages/eventstore -q -rs` passes with no `SKIPPED` line, including `test_replay_inserts_no_row` (every golden event of `contracts/events/vectors/golden/` inserted, then inserted again, gives zero new rows and a duplicate count equal to the number of events), `test_time_from_the_fact_not_the_write_clock` (the same id with a write-time clock adds a second row, and the event with its own `time` does not), `test_time_survives_to_the_microsecond`, `test_body_is_byte_exact`, `test_batch_of_ten_thousand_in_one_call` and `test_mixed_new_and_duplicate_counts`.
- [ ] The golden events of the envelope contract pass through `insert_events` unchanged, which closes the follow-up that the envelope closure records for this epic.
- [ ] A batch with an event over 64 KiB is refused before the database by the envelope's own check, shown by `test_oversized_event_is_refused_before_insert`.

Test: `packages/eventstore/tests/`.

### E.3 Compressed chunks and retention behaviour

Lane E, tier T1.
Needs: E.2.

Scope: behaviour of the hypertable once data ages: a duplicate that lands in a compressed chunk inserts nothing, the retention policy drops whole chunks, and `chunk_count` reports them.
The tests force compression and retention by running the policy jobs directly, never by waiting.

Owns: `packages/eventstore/**`.

Acceptance criteria:

- [ ] `pytest packages/eventstore -q -rs` passes with no `SKIPPED` line, including `test_duplicate_into_a_compressed_chunk_inserts_nothing`, `test_retention_drops_whole_chunks_older_than_the_window` and `test_chunk_count_follows_compression_and_drop`.
- [ ] If a duplicate does insert a row into a compressed chunk on the pinned image, the test fails and the PR records the image version and the workaround, so a silent double row is never accepted.
- [ ] `test_events_for_job_uses_the_job_index` shows the lookup of one job's events by `EXPLAIN` using an index and not a scan of every chunk.

Test: `packages/eventstore/tests/`.

### A.1 Artifact index table with compile cache entries

Lane A, tier T1.
Needs: lane 0.
Decision 6 applies.

Scope: migration `0300_artifact_index.sql` with the one table `artifact` as in section 0.3 and its static row-shape checks (the reference branch tests `kind <> 'compile-cache'` and holds no kind list), a generated repeatable file `R__artifact_kinds.sql` that replaces the check on `kind` from `contracts/artifact/layout.yaml` plus `compile-cache` and the producer-nullness check generated from the addressing class of each kind (written by `python -m wdf_artifactindex.gen`; `0300` holds no kind list of either class and names only `compile-cache`, whose cache branch of the `CHECK` holds the three producer columns null), and the library `index_reference`, `mark_deleted`, `references_to`, `record_cache_entry` and `touch_cache_entry`.
A reference is validated through `wdf_events.models` before it is stored.
The library and `wdf_events.models` keep their own ten-kind rule, because the models type the kind of a reference as a fixed ten-kind literal in a package that lane A does not own, so a kind added to `layout.yaml` is accepted by the table and not by the library until the envelope owners change the models.
The first writer of a cache key wins, and `record_cache_entry` writes the three producer columns as null.
A row is soft deleted by `deleted_at` and never removed by this library, `mark_deleted` returns `Pinned` and changes nothing while a live cache entry names the wasm, and `record_cache_entry` returns `WasmGone` for a wasm that is missing or soft deleted, both in one statement with the wasm row locked `FOR SHARE`.

Owns: `packages/artifactindex/**`, `contracts/state/vectors/artifacts/**`.

Acceptance criteria:

- [ ] `python -m wdf_artifactindex.gen --check` exits 0, and after adding a kind to `layout.yaml` in a scratch copy it exits 1 naming `R__artifact_kinds.sql`.
- [ ] `pytest packages/artifactindex -q -rs` passes with no `SKIPPED` line, including `test_every_valid_artifact_vector_is_indexed` (every file of `contracts/artifact/vectors/valid/`), `test_every_invalid_artifact_vector_is_refused`, `test_attempt_identity_is_stored_as_given` (two runs of one task give two rows `r0a1` and `r0a2` and neither is derived), `test_attempt_columns_have_no_default`, `test_first_writer_wins_on_a_cache_key`, `test_mark_deleted_is_refused_while_a_cache_entry_names_the_wasm`, `test_record_cache_entry_refuses_a_soft_deleted_wasm`, `test_raw_delete_of_a_pinned_wasm_is_refused_by_the_foreign_key`, `test_concurrent_mark_deleted_and_record_cache_entry_leave_no_pinned_deleted_wasm`, `test_a_row_is_either_a_reference_or_a_cache_entry`, `test_a_cache_row_has_null_producer_columns` (a raw insert of a cache row with any producer column set is refused, and `record_cache_entry` writes none), `test_a_kind_added_to_a_scratch_layout_is_accepted_by_the_table` (a content-addressed and an attempt-scoped kind added to a scratch `layout.yaml`, regenerated and applied, then raw inserts into the table with and without producer columns, accepted and refused as the class demands; the test does not go through the library), `test_mark_deleted_keeps_the_row` and `test_ddl_conformance`.
- [ ] `test_content_addressed_kinds_have_no_producer_and_attempt_scoped_kinds_have_one` holds at the constraint level, run against the table after `R__artifact_kinds.sql` is applied, and `test_0300_holds_no_kind_list` fails when `0300_artifact_index.sql` contains any kind name read from `layout.yaml` as a quoted literal and exempts `compile-cache`, the one kind that `0300` names by design.

Test: `packages/artifactindex/tests/`.

### D.1 App Postgres with TimescaleDB module

Lane D, tier T2.
Needs: lane 0, R.1 and J.1 (its acceptance reads the `job_status` table that J.1 creates).
Decision 4 and the TimescaleDB row of section 0.3 apply.

Scope: `deploy/appdb/module.yaml` and `Tiltfile.module`: a StatefulSet on the infra pool with the pinned image, a volume claim and requests and limits from the S8 result, telemetry off by setting, a readiness probe, credentials from a generated Secret (`scripts/storage/db/secrets.sh`), and the helper module `deploy/appdb/migrate/` (`appdb-migrate`), whose `local_resource` waits for readiness, opens a loopback port-forward and runs `scripts/storage/db/migrate.sh`, which calls `wdf_migrate apply`.
`scripts/storage/db/fsync.sh` runs `pg_test_fsync` in the pod on its volume claim and prints the figures.

Owns: `deploy/appdb/**`, `scripts/storage/db/**`.

Acceptance criteria:

- [ ] `contracts/module/check.sh deploy/appdb/module.yaml` and `contracts/module/check.sh deploy/appdb/migrate/module.yaml` exit 0.
- [ ] T2: `scripts/tilt/run.sh ci` exits 0 with the resource `appdb` healthy and the resource `appdb-migrate` completed, and no pod of the module is Pending or rejected by the quota (`kubectl --context kind-wdf -n wdf-infra get events` shows no `FailedCreate` or `Insufficient`).
- [ ] T2: `kubectl --context kind-wdf -n wdf-infra exec statefulset/appdb -- psql -U postgres -tAc "select count(*) from job_status"` prints 10, and `show timescaledb.telemetry_level` prints `off`.
- [ ] T2: after `just down` and `just up` and the same Tilt run, a marker row written before survives, and `just nuke` removes the data directory.
- [ ] `scripts/storage/db/fsync.sh` prints its figures and `df` of the data volume inside the pod, which the PR pastes and Closure records, with no pass line.
- [ ] No tracked file under `deploy/appdb/` or `scripts/storage/db/` holds a credential, shown by `pytest packages/migrate -q -k no_credentials_in_tracked_files`.

Test: the commands above, and `shellcheck` through `just lint` for the scripts.

### D.2 Temporal's Postgres module

Lane D, tier T2.
Needs: lane 0 and R.1.

Scope: `deploy/temporaldb/`: plain Postgres at the already pinned `postgres` 16.14, one StatefulSet on the infra pool with 1 GiB of memory (S3b), a 5 GiB volume claim, the two databases `temporal` and `temporal_visibility`, readiness and a generated Secret.
Temporal's schema and server are #6's.

Owns: `deploy/temporaldb/**`, `scripts/storage/db/**`.

Acceptance criteria:

- [ ] `contracts/module/check.sh deploy/temporaldb/module.yaml` exits 0.
- [ ] T2: `scripts/tilt/run.sh ci` exits 0 with the resource `temporaldb` healthy and no `FailedCreate` event in `wdf-infra`.
- [ ] T2: `kubectl --context kind-wdf -n wdf-infra exec statefulset/temporaldb -- psql -U postgres -tAc "select datname from pg_database where datname like 'temporal%' order by 1"` prints `temporal` and `temporal_visibility`.
- [ ] The `wdf-infra` requests and limits of all modules added so far sum to at most the quota, shown by the output of `kubectl --context kind-wdf -n wdf-infra describe resourcequota pool-capacity` in the PR.

Test: the commands above.

### O.1 Object storage module

Lane O, tier T2.
Needs: lane 0, R.1 and C.1.
Decision 15 and questions 1 and 4 apply.

Scope: `deploy/objectstore/`: the store as a StatefulSet on the infra pool with the pinned image, `-volume.max=0` and the volume size limit of the sizing formula of section 0.3, the identities file rendered from `contracts/store/roles.yaml` into a generated Secret by `scripts/storage/store/identities.sh`, a `weed admin` container and a `weed worker` container running the `s3_lifecycle` job with the detection interval of 60 minutes (the store, the admin and the worker are the three containers of one StatefulSet pod, each with its own readiness probe, so the one module `objectstore` and its one Tilt resource cover all three), and the helper module `deploy/objectstore/bootstrap/` (`objectstore-bootstrap`), whose `local_resource` creates the four buckets from `contracts/artifact/layout.yaml` and applies `contracts/store/lifecycle.yaml` with the `admin` identity.
`deploy/objectstore/README.md` holds the data volume path inside the `store` container (which Q.3 reads with `df`) and the runbook: recovery from a full disk (delete objects, then `volume.vacuum` through `weed shell`), rotating a credential, a late or dead lifecycle worker as a capacity alert, and the lifecycle edit gap of question 4.

Owns: `deploy/objectstore/**`, `scripts/storage/store/**`.

Acceptance criteria:

- [ ] `contracts/module/check.sh deploy/objectstore/module.yaml` and `contracts/module/check.sh deploy/objectstore/bootstrap/module.yaml` exit 0.
- [ ] T2: `scripts/tilt/run.sh ci` exits 0 with `objectstore` healthy, `objectstore-bootstrap` completed and no `FailedCreate` event in `wdf-infra`.
- [ ] T2: `kubectl --context kind-wdf -n wdf-infra get pod objectstore-0 -o jsonpath='{.status.containerStatuses[*].ready}'` prints `true true true`, so the store, the admin and the lifecycle worker are all running.
- [ ] T2: `python -m wdf_storecheck run --endpoint http://127.0.0.1:<forwarded port> --credentials <state file> --mode base` reports 14 of 14, including a first write to each of the four buckets with the identity that owns it.
- [ ] T2: `--mode access` reports no `FAIL` line and one `known-gap` line per role that holds `Write`, under the default of question 4.
- [ ] The idle memory of the three containers is recorded in the PR next to the 131 MiB of spike S5.
- [ ] The runbook's commands are the ones that C.2's `recover` mode runs, and the reviewer checks that they agree.

Test: the commands above, and `pytest packages/migrate -q -k no_credentials_in_tracked_files`.

### C.1 S3 contract script: base and access

Lane C, tier T1.
Needs: lane 0 and R.2.
Question 4 applies.

Scope: `python -m wdf_storecheck run --mode base` and `--mode access`, runnable against any endpoint: the 14 base checks of spike S5, the role matrix of `contracts/store/roles.yaml` (every role against every bucket for put, get, list, multipart and delete), a wrong secret and an anonymous client, the rule that a wrong-role write is refused with `AccessDenied` and leaves no object behind, and the lifecycle edit by a writing role, which is a `known-gap` for the products that `products.yaml` lists and a failure for the others.
Provisioning for the T1 tests: SeaweedFS from an identities file mounted read-only (R.2), and RustFS through its signed admin API, as S5 did.
The script is rebuilt from the method of `docs/spikes/object-store.md`, because the S5 files are not in the repository.

Owns: `packages/storecheck/**`.

Acceptance criteria:

- [ ] `pytest packages/storecheck -q -rs` passes with no `SKIPPED` line, with `test_base_checks_14_of_14[seaweedfs]`, `test_base_checks_14_of_14[rustfs]`, `test_wrong_role_write_is_refused_and_leaves_nothing[seaweedfs]` and the same for `rustfs`, and `test_role_matrix_equals_the_contract[seaweedfs]` and `[rustfs]`.
- [ ] `test_writer_can_edit_lifecycle_is_a_known_gap_on_seaweedfs` passes and `test_writer_cannot_edit_lifecycle_on_rustfs` passes, and flipping either behaviour fails the matching test.
- [ ] `python -m wdf_storecheck run --endpoint URL --credentials FILE --mode base` exits 2 with a usage line when an option is missing, and 1 against a closed port.
- [ ] `boto3` lands in this PR's lock change, and `uv lock --check` exits 0.
- [ ] `docker ps -a --filter label=wdf.dev/verify-run` lists no container after the run.

Test: `packages/storecheck/tests/` (T1, two products, one container at a time).

### C.2 S3 contract script: lifecycle, `Days`, full disk and recovery

Lane C, tier T1 and T2.
Needs: C.1 and O.1.

Scope: modes `lifecycle` (a past-dated `Date` rule on one prefix with three controls in other prefixes and buckets, polled until the expiry), `days-arm` (a `Days` rule with objects written and the expected expiry time recorded in a file) and `days-check` (verifies that file after at least 25 hours), `disk-full` and `recover` (a size-capped tmpfs filled with 4 MiB objects until a put fails, then refusals, a batch delete, `volume.vacuum` run through `exec`, and a put that succeeds again), `marker-put` and `marker-get` for the persistence check of the exit test, and `capacity`, which prints one `bytes <bucket> <n>` line per bucket from the S3 listing, always with the `reaper` identity of the credentials file because that identity lists all four buckets (`admin` also does and is for provisioning only), for the inputs of Q.3.
T1 runs disk-full and recover on both products and records that RustFS cannot delete while full.
T2 runs `lifecycle` against the deployed store with its admin and worker.
The `days-check` half is run by the characterization soak (#16) and not by this epic.

Owns: `packages/storecheck/**`.

Acceptance criteria:

- [ ] `pytest packages/storecheck -q -rs` passes with no `SKIPPED` line, with `test_disk_full_refuses_with_500_and_keeps_survivors[seaweedfs]`, `test_batch_delete_works_while_full_on_seaweedfs`, `test_recover_needs_vacuum_on_seaweedfs`, `test_delete_fails_while_full_on_rustfs` and `test_lifecycle_date_rule_expires_only_the_armed_prefix[rustfs]`.
- [ ] T2: `python -m wdf_storecheck run --endpoint ... --credentials ... --mode lifecycle` against the deployed store prints `expired: 1 of 1, controls kept: 3 of 3` and exits 0.
- [ ] `python -m wdf_storecheck run --mode days-arm` writes the file that `--mode days-check` reads, and `--mode days-check` run at once exits 1 with `not yet due`, so the half run by #16 is proven to refuse an early check.
- [ ] T2: `--mode capacity` against the deployed store prints a `bytes` line for each of the four buckets and exits 0, and it exits 1 when the credentials file has no `reaper` identity.
- [ ] T2: `--mode marker-put`, then `just down`, `just up`, `scripts/tilt/run.sh ci`, then `--mode marker-get` exits 0 with the object intact.
- [ ] If the lifecycle job has no on-demand trigger on the deployed store, the PR says so and the T2 run waits one detection interval, recorded in the PR.

Test: `packages/storecheck/tests/` (T1) and the commands above (T2).

### Q.1 Invariant and drift queries, and the composition test

Lane Q, tier T1.
Needs: J.2, E.2 and A.1.

Scope: the five queries of section 0.3 as SQL files under `contracts/state/invariants/`, run by `python -m wdf_stateops.invariants`, which prints one `invariant: <name>: <key>` line per offending row.
The open workflow ids come from `--open-workflows FILE`, so nothing here reads Temporal.
The composition test proves that a row change and its events commit or roll back together when the caller uses one transaction, which is the rule the dispatch epic relies on.

Owns: `packages/stateops/**`, `contracts/state/invariants/**`, `contracts/state/vectors/invariants/**`.

Acceptance criteria:

- [ ] `pytest packages/stateops -q -rs` passes with no `SKIPPED` line, including `test_clean_database_reports_nothing`, `test_running_job_without_an_open_workflow_is_reported`, `test_open_workflow_of_a_terminal_row_is_reported`, `test_accepted_and_dead_lettered_jobs_are_never_reported`, `test_without_a_workflow_list_the_two_workflow_checks_are_skipped`, `test_published_event_without_a_receipt_is_reported`, `test_result_ready_event_without_an_artifact_row_is_reported`, `test_accepted_event_class_differing_from_the_row_is_reported` and `test_row_and_event_commit_or_roll_back_together`.
- [ ] `python -m wdf_stateops.invariants --dsn DSN` exits 0 on a clean database and 1 on one with a seeded violation, and 2 without `--dsn`.
- [ ] Each violation test seeds exactly one offending row and expects exactly one output line.

Test: `packages/stateops/tests/`.

### Q.2 Batch bench for 100,000 jobs

Lane Q, tiers T1 and T2.
Needs: Q.1 and D.1.

Scope: `python -m wdf_stateops.bench --jobs N [--dsn DSN] [--twice]` inserts N accepted rows in chunks of 1,000, each chunk in one transaction with its accepted events through the J and E libraries, then the remaining events of a two-task job (14 event rows per job as in the epic's estimate), and prints the rows and events inserted, the seconds of each phase, the table sizes and the chunk count.
Without `--dsn` it starts its own container through `wdf_itest`, and with `--twice` it runs the insert a second time in the same invocation.
It asserts counts and idempotence only and has no timing guard, because the epic asks for no performance gate (decision 16).
A pytest test runs the bench at `--jobs 2000 --twice` so the counting code runs in `just ci`.

Owns: `packages/stateops/**`.

Acceptance criteria:

- [ ] `python -m wdf_stateops.bench --jobs 100000 --twice` exits 0 and prints `rows_inserted: 100000` and `events_inserted: 1400000` for the first run and `rows_inserted: 0` and `events_inserted: 0` for the second, because the keys are deterministic.
- [ ] `pytest packages/stateops -q -rs -k bench` passes with no `SKIPPED` line, including `test_bench_exits_1_when_a_count_is_wrong` (a stub that drops one event).
- [ ] T2: `python -m wdf_stateops.bench --jobs 2000 --dsn DSN` run twice against the app database of the deployed cluster, through a port-forward bound to 127.0.0.1, prints the counts of 2,000 rows and 28,000 events the first time and zero and zero the second time.
- [ ] The measured numbers of the 100,000 job run are pasted in the PR and in Closure, as an estimate of the insert and ingest rates that the epic lists as unmeasured.

Test: `packages/stateops/tests/`.

### Q.3 Metric and alert inputs

Lane Q, tiers T1 and T2.
Needs: J.1, E.1, A.1, Q.1, O.1 and C.2.
The drift input is the row count of the queries of Q.1 over the table of A.1, and the store inputs come from the `capacity` mode of C.2 and from `df` in the store pod of O.1, both against the deployed store, so this issue waits for them instead of shipping placeholders.

Scope: `contracts/state/inputs.yaml` with its generated schema and loader, listing the inputs of section 0.3 with unit, source and threshold, and `python -m wdf_stateops.inputs --dsn DSN`, which prints `<name> <value>` per input.
The store inputs (bytes per bucket and the capacity share, alerting at 70 percent) take the bytes per bucket from `python -m wdf_storecheck run --mode capacity`, which C.2 delivers, and the capacity share from `df` of the store's data volume inside the pod (`kubectl --context kind-wdf -n wdf-infra exec objectstore-0 -c store -- df`), the volume path being the one that the O.1 README names.
The metric names and exporters belong to the observability epic (#13).

Owns: `packages/stateops/**`, `contracts/state/{inputs.yaml,inputs.schema.json}`, `contracts/state/vectors/inputs/**`.

Acceptance criteria:

- [ ] `pytest packages/stateops -q -rs -k inputs` passes with no `SKIPPED` line, including `test_every_sql_input_returns_a_number_on_a_fresh_database` and `test_ingest_lag_grows_with_the_age_of_the_newest_event`.
- [ ] `python -m wdf_stateops.gen --check` exits 0.
- [ ] `python -m wdf_stateops.inputs --check contracts/state/inputs.yaml` exits 0, and every file under `contracts/state/vectors/inputs/invalid/<reason>/` exits 1 with that `reason:`.
- [ ] T2: `python -m wdf_stateops.inputs --dsn DSN --store-endpoint URL --credentials FILE` prints a value for every input including one line per bucket for bytes and the capacity share, against the deployed module.

Test: `packages/stateops/tests/`.

### Q.4 Group roles and grants

Lane Q, tier T1.
Needs: J.1, E.1 and A.1.
Question 3 applies (default (a)).

Scope: the repeatable migration `R__roles.sql`, hand written and shipped as package data of `stateops`: the `NOLOGIN` roles of the writers table of section 2, each created only when absent (a `DO` block over `pg_roles`) and every column grant applied again on each run.
No `writers.yaml`, schema or generator is built: the writers table of `contracts/state/README.md` is the statement of who writes what, and a catalog test holds the file to it (decision 17).
The test connects as a stand-in login user in each group.

Owns: `packages/stateops/**`.

Acceptance criteria:

- [ ] `pytest packages/stateops -q -rs -k grants` passes with no `SKIPPED` line, including `test_workflow_role_cannot_insert_an_accepted_job`, `test_dispatch_role_cannot_update_status`, `test_control_role_writes_only_the_control_columns_and_row_only_moves`, `test_admission_role_inserts_events_and_moves_to_admitted`, `test_reaper_role_updates_only_deleted_at`, `test_reader_role_cannot_write_any_table` and `test_every_table_of_the_three_families_has_a_writer` (a catalog query over `information_schema.role_table_grants` that finds a group role with `INSERT` on each of `accepted_job`, `event` and `artifact`).
- [ ] `test_roles_apply_again_in_a_second_database_of_the_same_container` shows `python -m wdf_migrate apply` exiting 0 on a second database, so the cluster wide roles do not collide.
- [ ] `test_status_value_is_not_restricted_by_grants` records the stated limit, so the claim of the writers table is the one the tests prove.

Test: `packages/stateops/tests/`.

### X.1 State modules workflow

Lane X, tier T2.
Needs: D.1, D.2, O.1 and C.1.
Question 5 applies (default (c)).

Scope: `.github/workflows/state-modules.yml`, triggered on pull requests that touch `deploy/appdb/**`, `deploy/temporaldb/**`, `deploy/objectstore/**`, `contracts/store/**`, `contracts/state/**`, `packages/storecheck/**`, `packages/migrate/**` and `scripts/storage/**`, and on pushes to `main`.
One job runs `just up` on `ci`, `scripts/tilt/run.sh ci`, the contract script in `base` and `access` modes against the deployed store, `wdf_migrate check` against the deployed app database, and `just down`, with the cluster log export on failure that `kind-smoke` has.
It honours the `CI_ENABLED` variable like the other workflows.

Owns: `.github/workflows/state-modules.yml`.

Acceptance criteria:

- [ ] `just lint` exits 0 with `actionlint` accepting the file.
- [ ] A run of the workflow on the PR branch is green, and its URL is in the PR.
- [ ] The job has `timeout-minutes` set and uploads the cluster logs only on failure.
- [ ] After merge, one green run on `main` is linked in Closure.

Test: the workflow run itself.

## 5. Order, stubs and spikes

- First, in parallel: M.1 (one worker, a one day timebox), lane 0's issue 0.1 (which needs nothing) followed by 0.2, and R.2 (which needs nothing).
  R.1 follows M.1.
  0.3 follows M.1 and 0.1, and is the last issue of lane 0.
- Barrier: lane 0 (0.1, 0.2, 0.3), one worker, merged alone.
  The estimate is four days, with M.1 done on the first.
- After lane 0: lanes J, E and A start together and need no cluster.
  Lane C starts with C.1 once R.2 has merged.
  Lanes D and O wait for R.1.
- DAG: (M.1 ‖ 0.1, then 0.2 ‖ R.2), then (R.1 after M.1 ‖ 0.3 after M.1 and 0.1), then lane 0 merged, then (J.1, then J.2 ‖ E.1, then E.2, then E.3 ‖ A.1 ‖ C.1 after R.2 ‖ D.1 after R.1 and J.1, D.2 after R.1), then (O.1 after R.1 and C.1), then (C.2 after O.1 ‖ Q.4 after J.1, E.1 and A.1 ‖ Q.1 after J.2, E.2 and A.1), then (Q.2 after Q.1 and D.1 ‖ Q.3 after A.1, Q.1, O.1 and C.2 ‖ X.1 after D.1, D.2, O.1 and C.1), then the exit test.
- Cluster-using work (R.1, D.1, D.2, O.1, C.2's T2 half, the T2 halves of Q.2 and Q.3, X.1 and the T2 exit rows) takes `flock /tmp/wdf-cluster.lock` and runs one at a time on this host.
  T1 work waits for one of the two container slots of `wdf_itest`, so at most two T1 containers run on the host whatever the number of workers.
- Question 1 is answered before 0.1 merges, question 2 before 0.3 starts, question 3 before Q.4 starts, question 4 before 0.2 merges and question 5 before X.1 is filed, or the defaults apply.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| The library functions and the models-only skeletons of the six packages that 0.1 creates (`migrate`, `jobstate`, `eventstore`, `artifactindex`, `storecheck`, `stateops`) | `NotImplementedError("not implemented: owned by lane J")` and the same for E, A, C and Q | each package's `test_vectors.py` accepts that error only for the names in its `STUBBED` list and a real match for the rest, and the owning issue empties the list |
| Table DDL before J.1, E.1 and A.1 | no table exists | each package's `test_ddl_conformance` accepts a missing table only for the names in its `STUBBED` list, and compares columns, types, nullability and defaults to the pinned model for the rest |
| The migration fixture package in the tests of `migrate` | a package with two numbered files and one repeatable file | the same runner tests run against the real packages through the `wdf.migrations` entry points once they exist |
| Open workflow ids for the drift query | a file list that stands for Temporal visibility | #16 and #17 pass the real list from Temporal, and the query code is the same |
| Stand-in login users for the group roles | users created by the test and added to each group | the service epics create the login roles, and the grants they receive are the ones tested here |

Spikes: M.1 is the one spike of this epic, S8, numbered after S7 of the method, and it gates R.1, 0.3 and the module lanes.
The S5 result is applied by 0.2, O.1, C.1 and C.2.
The `Days` rule on aged objects and the abort of incomplete multipart uploads are accepted but not observed by S5, and the arming half of the first is delivered here while the observation is #16's.

## 6. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main` taken outside any git repository, with Docker running and the cluster free.
Each command runs from the export root as written, its real output is pasted in the PR that records the run, and it is summarised in Closure.
Rows 13 to 21 take the cluster lock for their whole sequence, and row 10 is run inside that sequence after row 14, for example `flock /tmp/wdf-cluster.lock env WDF_CLUSTER_LOCK_HELD=1 bash -c '<commands>'`, because `just up`, `just down` and `just nuke` take the lock themselves unless that variable is set and would otherwise wait 900 s on the lock that their own caller holds.
The steps that change a file do so in the scratch export and restore it afterwards.
The export is of `main` after every lane has merged, in the order of section 5.
`DSN` stands for the database that a step names and `<port>` for the loopback port of a `kubectl port-forward` that the step opens.
Command prefixes `nix develop -c` are omitted below and every row takes them.

| # | Command | Expected |
| --- | --- | --- |
| 1 | `uv sync --frozen` then `uv lock --check` | exit 0 for both, lock unchanged |
| 2 | `for p in jobstate eventstore artifactindex storecheck stateops; do uv run --frozen python -m wdf_$p.gen --check \|\| echo "DRIFT $p"; done` | no output |
| 3 | rename a field of `JobRow`, run `uv run --frozen python -m wdf_jobstate.gen --check`, then restore the file | exit 1 naming `contracts/state/schemas/job-row/1.json` |
| 4 | add an eleventh entry to `statuses` of `contracts/status/status.yaml`, run `uv run --frozen python -m wdf_jobstate.gen --check`, then restore the file | exit 1 naming `R__job_vocab.sql`, which shows the vocabulary is generated and never retyped |
| 5 | `contracts/state/check.sh contracts/state/retention.yaml`, `python -m wdf_stateops.inputs --check contracts/state/inputs.yaml`, `contracts/store/check.sh` on `roles.yaml`, `lifecycle.yaml` and `products.yaml`, then every file under `contracts/state/vectors/retention/invalid/` through `contracts/state/check.sh`, every file under `contracts/state/vectors/inputs/invalid/` through the inputs loader and every file under `contracts/store/vectors/invalid/` through `contracts/store/check.sh` | exit 0 for the five valid files, and exit 1 with a `reason:` line for every invalid file |
| 6 | `uv run --frozen pytest packages/migrate packages/jobstate packages/eventstore packages/artifactindex packages/storecheck packages/stateops packages/itest -q -rs` | all pass with no `SKIPPED` line, covering the runner, the three table families, the grants, the invariants and both object store products |
| 7 | `uv run --frozen pytest packages/jobstate packages/eventstore packages/artifactindex packages/migrate -q -v -k "ten_of_the_contract or late_writer_loses or every_contract_transition or replay_inserts_no_row or time_from_the_fact or compressed_chunk or attempt_columns_have_no_default or from_empty_and_from_previous_release or first_writer_wins"` | each named test is listed `PASSED` |
| 8 | `uv run --frozen pytest packages/migrate packages/jobstate packages/eventstore packages/artifactindex packages/storecheck packages/stateops -q -rs -k wheel` | all pass, none skipped, so every package works from a built wheel |
| 9 | `uv run --frozen python -m wdf_stateops.bench --jobs 100000 --twice` | exit 0, `rows_inserted: 100000` and `events_inserted: 1400000`, then `rows_inserted: 0` and `events_inserted: 0` for the second run, with the phase times printed for the record |
| 10 | after row 14, `uv run --frozen python -m wdf_stateops.bench --jobs 2000 --dsn DSN` twice against the app database of the cluster through a port-forward | exit 0 both times, 2,000 rows and 28,000 events the first time and zero and zero the second |
| 11 | `for p in ci dev soak; do contracts/preset/check.sh deploy/presets/$p.yaml; scripts/cluster/slots.sh $p; done` and `scripts/cluster/namespaces.sh render ci \| grep -A8 'namespace: wdf-infra' \| grep limits.memory` | exit 0 for all, every slot reported as fitting, and the quota equals the figure of the S8 table for `ci` |
| 12 | `just doctor` | exit 0 with `memory-vs-preset` reading `ok` |
| 13 | `just up`, then `kubectl --context kind-wdf get ns wdf-infra wdf-control wdf-workers wdf-obs`, then `just up` again | exit 0, four namespaces listed, the second `just up` leaves every node container id unchanged |
| 14 | `scripts/tilt/run.sh ci` | exit 0 with `appdb`, `temporaldb` and `objectstore` healthy, `appdb-migrate` and `objectstore-bootstrap` completed, no `FailedCreate` event in `wdf-infra`, and `kubectl --context kind-wdf -n wdf-infra get pod objectstore-0 -o jsonpath='{.status.containerStatuses[*].ready}'` printing `true true true` |
| 15 | `kubectl --context kind-wdf -n wdf-infra exec statefulset/appdb -- psql -U postgres -tAc "select count(*) from job_status"`, `... "show timescaledb.telemetry_level"` and `... "select count(*) from timescaledb_information.jobs where hypertable_name = 'event'"` | `10`, `off`, and a count of at least 2 (the compression and the retention policy) |
| 16 | `uv run --frozen python -m wdf_migrate check --dsn DSN` against the app database through a port-forward | exit 0 |
| 17 | `uv run --frozen python -m wdf_storecheck run --endpoint http://127.0.0.1:<port> --credentials <state file> --mode base`, then `--mode access` | `14 of 14`, then no `FAIL` line and one `known-gap` line per role that holds `Write` |
| 18 | `uv run --frozen python -m wdf_storecheck run ... --mode lifecycle` against the deployed store | `expired: 1 of 1, controls kept: 3 of 3`, exit 0, which only passes with the lifecycle worker running |
| 19 | `uv run --frozen python -m wdf_stateops.inputs --dsn DSN --store-endpoint http://127.0.0.1:<port> --credentials <state file>` | a value for every input of `inputs.yaml`, with a line per bucket for bytes and the capacity share |
| 20 | `scripts/storage/db/fsync.sh` while the cluster is up | prints its figures and `df` of the data volume, which are recorded in Closure, with no pass line |
| 21 | the destructive last row: a marker row in `event` through `psql` and `--mode marker-put`, then `just down`, `just up`, `scripts/tilt/run.sh ci`, then the row again and `--mode marker-get`, then `just nuke` twice | the row and the object survive, `just nuke` leaves nothing and repeats cleanly |
| 22 | `just ci` | exit 0 |
| 23 | the `ci` workflow and the `state-modules` workflow on `main` | one green run of each, the URLs recorded in Closure |

Not provable by this epic: the `Days` lifecycle rule on objects a day old and the abort of incomplete multipart uploads (the arming half of the first is delivered here and #16 runs the check), disk behaviour on a real Kubernetes volume claim and a restart while full, a Postgres, TimescaleDB or object store outage (#15), growth over a multi-day soak and the real event rows per job and insert rate through the dispatch API (#16, #24, #6), the grants as used by real services, prefix-level restriction of a role, the drift comparison with Temporal visibility itself, fsync on a host other than this WSL2 one, any replication or backup, and a maximum job duration above 7 days.

## 7. Parallelism and cost

- Maximum parallel workers: 3 for the first phase (M.1, lane 0 and R.2), then 2 with R.1, then up to 6 after lane 0 (J, E, A, C, D and Q), planned at 5 because the cluster is single-owner and the host runs two T1 containers at a time, then 3 for O.1, C.2 and Q.1 to Q.4, then 1 for the exit test.
- Host constraints: T0 work needs only `uv` and Python from the dev shell.
  T1 work starts TimescaleDB, SeaweedFS or RustFS by `docker run` on 127.0.0.1, each capped at 1 GiB, with at most two containers across the host.
  T2 work needs the `ci` cluster, whose sum of node limits is what R.1 sets, on a host that holds it, so a worker never starts `dev` or `soak`.
- CI cost, stated as estimates until M.1 records start times and image sizes: every lane's `just ci` now runs the T1 tests of these packages, which start, one after the other, one TimescaleDB container per test module that uses the `appdb` fixture (several modules in each of `migrate`, `jobstate`, `eventstore`, `artifactindex` and `stateops`, so the count is the number of such modules and is not estimated here) and SeaweedFS and RustFS containers for the contract tests and the full disk runs, and a clean runner pulls three more images.
  The `state-modules` workflow of X.1 (question 5) is the only addition that starts a cluster, and it runs only on the paths of this epic and on `main`.
  The plan keeps module scoped containers with a fresh database per test, and no test waits for a policy to fire, so the added time stays at the container starts.
- Estimates, not measurements, in worker-days: M.1 (1), R.1 (0.5), R.2 (0.5), lane 0 (4), lane J (2.5), lane E (3), lane A (1.5), lane D (2), lane O (2), lane C (4), lane Q (3.5), lane X (0.5), exit test (1).
  That is about 26 worker-days of effort and about 11 days elapsed along lane 0, lane E, lane Q, C.2, X.1 and the exit test, with the cluster lock, the two container slots and `uv.lock` rebases as the main sources of slip.

## 8. Closure (filled at close)

Not yet run.
At close the closing worker also refines the architecture note `docs/architecture/13-state-envelope-artifacts.drawio` with the infra node resize, the app database with TimescaleDB, the object store choice and its roles, and re-exports the diagrams (`scripts/export-diagrams.sh --check` clean).
No lane owns the note, so this is a close step and not a lane issue.
At close this section holds the commit of the last lane merge, the run of the exit test with its pasted output in the PR that records it, the fsync figures of D.1, the measured insert and ingest rates of Q.2, the S8 numbers that resized the infra node, the URLs of the two green workflow runs on `main`, and what moved.
It also records the follow-ups that this spec already knows:

- scaling the maximum job duration beyond 7 days changes `max_job_duration_days`, the compat window and every window of `retention.yaml`, and the retention loader fails first on a window below twice the maximum, which is the intended tripwire
- the upper-bound check of the declared job class, which the shipped validator lacks (it has only `unknown-class`), a follow-up outside this epic that comes with a closure addendum line in `docs/epics/04-validator.md` recording that decision 8 there is superseded
- the reconciliation of the accepted event's `jobclass` attribute with the validator's `classes` (the golden `job.accepted` vector carries `jobclass: simulate`, outside `[short, long]`), which needs a contract and vector change by the envelope and validator owners and is outside this epic
- the ten-kind rule of the library and of `wdf_events.models`, which keeps the library from indexing a kind that a later `layout.yaml` adds although the table accepts it, and which the envelope owners change when a kind is added
- the follow-up edit of `docs/method.md` that three specs now name
- the `Days` check half that #16 runs, and the on-demand trigger of the lifecycle job if it needed a wait
- the login roles and credentials of the service epics, which receive the grants tested here

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).
