# Epic #4: Job event envelope (CloudEvents) and state propagation - breakdown

Status: draft
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/4
Architecture note: docs/architecture/13-state-envelope-artifacts.drawio
Depends on (closed epics): Dev environment (#1, closed)

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-10-30) while preparing this spec, `[read]` means read and not re-run.
The epic issue body was read on 2026-10-30, and this spec was written against that version.
Method: [docs/method.md](../method.md).
Model: [docs/epics/02-cluster.md](02-cluster.md).
Spike results consulted: [broker client](../spikes/broker-client.md) (S4), [Temporal](../spikes/temporal.md) (S3, S3b) and [object store](../spikes/object-store.md) (S5).
This epic has no cluster dependency.
Its tests run at tier T0 (host Python, no runtime) and T1 (containers started by `docker run` on the host), and it never starts a cluster.

## 0. Decisions for the captain (max 5)

### 0.1 Recorded decisions

The captain reviewed the open questions of the epic before this spec was written.
The answers below were recorded on 2026-10-30 and are applied in this spec.
Everything else in the epic body stands, with one stated exception.
Question 3 of section 0.2 changes the interface rows of the ticket for #6 and #5, because under its default the workflow input becomes the accepted event plus a params block, where the ticket says the accepted event alone.

| # | Decision | Answer | Applied in |
| --- | --- | --- | --- |
| 1 | Encoding on the broker hop | AMQP 1.0 with the CloudEvents AMQP binding in structured mode and the JSON event format, confirmed | 0.1 |
| 2 | Job status values | The current list with `queued` renamed `accepted` (the stored list in #3 already reads `accepted`, so this fixes the wording of the older list), the terminal status `partial` added, and `pausing` and `cancelling` are views over a requested action and never stored statuses | 0.2 |
| 3 | Verifier | None for this epic, and the `verify-job-model` skill is dropped. The product tests stay: the schema drift check, golden events, the replay corpus and the key derivation property tests | sections 4 and 6 |
| 4 | Task request | A CloudEvent | E.3 |
| 5 | Schema source and serializer | Python models are the schema source and the serializer is hand written, so the CloudEvents Python SDK is not used | 0.1, E.1 |
| 6 | Major version overlap | No side-by-side emission of an old and a new major. Readers accept the current and the previous major | C.1 |
| 7 | `dataschema` | A stable identifier mapped to the repository and not a resolvable URL, and it carries the minor version | 0.1 |
| 8 | Source granularity | One `source` per component, never per pod or per tenant | 0.1, S.1 |
| 9 | Batch manifests | One shared template manifest per batch plus per-job parameters on the row, stated as an interface shared with #5 and #24 | section 2 |
| 10 | Resume counter | Lives in workflow state and is not derived from the event history | K.1 |
| 11 | Checkpoint events | This epic defines only the checkpoint committed event, and the cadence belongs to the checkpoint epic (#18) | E.2 |
| 12 | Placeholders | Maximum job duration 7 days and dead letter retention 14 days, until the captain sets real values | C.1 |

The trace backend retention question of the epic stays with the observability epic (#13).

Audit resolutions, applied after the captain reviewed an audit of the first draft:

- A reader resolves an unknown minor of a known major to the highest minor it knows and ignores added optional fields, while strictness (an unknown field is rejected, `dataschema` must map to a file) applies only within a minor the reader knows.
- Lane Needs across lanes and pins in lanes E, C and S are kept, and section 3 records them as departures from the method with their reasons.
- A contract-change PR for a generated schema touches `contracts/`, the model under `packages/` and the regenerated file together, and bumps the contract `VERSION`.
- Option (b) of question 3 needs a contract-change PR and contradicts decision 9, so the default of question 3 is firm unless the captain says otherwise.
- The `control` stage emits `resumed` for a job that never started, and the projection returns its row to `accepted`.
- The replay runner fails with a message naming the history and its owner when a history has an owners entry and no class was given, and never skips it.
- The T1 limit is a counting limit of two containers across the host, implemented as two lock files, a session takes whichever is free and waits with a timeout otherwise.
- A previous major stays readable for the dead letter retention plus the maximum job duration, 21 days at the placeholders.
- No job-level `dead.lettered` event is built for a message with no derivable job id, and the broker dead letter queue is the record in that case.
- The log field for the bare CloudEvents `id` is `cloudevent_id`, and the stored key keeps `event_id`.
- Every lane's Done gate runs `just ci`, which needs Docker once the T1 tests exist.
- The T1 port base is 19000, with slots 0 to 9 and `k` below 10, clear of `deploy/host/ports.yaml`.
- Issue 0.1 owns `contracts/artifact/reference.schema.json` and issue 0.2 owns the rest of `contracts/artifact/`.

### 0.2 New questions

None of these blocks the start of lane 0.
Each has a default that applies when there is no answer, and an answer that differs from the default changes only the issues named in the last column, except option (b) of question 3.

| # | Question | Options | Recommendation | Default if no answer | Changes |
| --- | --- | --- | --- | --- | --- |
| 1 | Is there a lifecycle event for a batch? The dispatch epic (#24) lists `dev.wdf.batch.submitted.v1` as open with this epic. | (a) per-job events only and the batch summary read from rows, (b) one batch event | (a). A batch has no workflow and no single writer of its end, a batch event is a fan-in fact that needs its own dedup rule, and the batch summary already comes from rows. | (a), so the catalogue gets no batch type | E.2, S.1 |
| 2 | What is the terminal event of a job that ended `partial`? The decision above adds the status and not its event. | (a) a new type `dev.wdf.job.partial.v1`, (b) `dev.wdf.job.succeeded.v1` with a `partial` flag in its data | (a). Every terminal status then has one terminal event and the status projection is a lookup by type. A consumer that filters on `succeeded` can never mistake a run on partial inputs for a complete one, which is the risk the workflow epic (#6) names for the opt-in aggregate. | (a) | 0.2, E.2 |
| 3 | How do the per-job parameters (seed, parameters, class override) reach the workflow, when the accepted event is a pointer and the workflow never reads Postgres? | (a) admission reads them from the row and passes a `job.params` data block as a second start argument beside the accepted event, (b) the accepted event data carries them, (c) the workflow loads them with an activity | (a). It keeps the event a pointer as decision 9 requires and keeps Postgres reads out of workflow code. The cost is that the epic's interface row "the workflow input is the accepted event" becomes "the accepted event plus the params block", and #6 and #8 confirm it in their specs. | (a) | E.2, T.1, and for option (b) the pinned accepted schema and the batch interface |
| 4 | How much of the instrumentation contract does lane 0 pin? The method pulls it forward into this epic's lane 0, and the epic body does not describe it. | (a) the rules only (metric naming, label policy, log field names, trace conventions) plus a `metrics.yaml` that holds only the metrics this epic defines, (b) the full platform catalogue | (a). The full catalogue needs components that do not exist yet, and #13 issue 1 extends the file and owns the contract after the pin. | (a) | 0.3 |
| 5 | The method lists `build-info.json` under the artifact contract of this epic and under the WIT contract of the toolchain epic (#11). Which lane 0 pins the file? | (a) #11 pins the file, and this epic pins the artifact reference, the bucket layout and the compile cache key, (b) this epic pins the file | (a). The file describes a compile, and #11 owns the compile. The four inputs of the cache key are the only part this epic needs. | (a) | 0.2 |

Option (b) of question 3 needs a contract-change PR on the accepted schema and on the batch interface (consumers #3, #5, #6, #8 and #24) and contradicts decision 9, so the default (a) is firm unless the captain says otherwise.

## 1. Goal and non-goals

Goal: one versioned event contract for the whole platform, built on CloudEvents 1.0.
Every platform event is a CloudEvent, and every stage can identify a job, its tenant, class, idempotency key and trace from the context attributes alone.
Events are immutable, the event history is append-only, and a producer and a consumer that disagree fail loudly at the edge and not inside a workflow.
Temporal's workflow state stays the authoritative copy of lifecycle, and the compatibility rules keep a job that outlives a deploy readable.

Terms: the event history is the append-only event store of #3, and the Temporal history is the workflow history inside Temporal.
The two are never used for each other.

Non-goals for this pass:

- everything the epic lists under "Does not own": the manifest content and DAG template (#5), workflow retries, versioning and continue-as-new (#6), queue behaviour and AMQP message properties (#7), table columns and the event store (#3), reference semantics and integrity (#12), the checkpoint contract (#18), the XML result blocks (#11), admission policy (#8) and the placement decision (#9)
- the checkpoint event cadence, an open question of the epic that decision 11 assigns to the checkpoint epic (#18)
- the broker hop itself: byte identity through RabbitMQ after publish, dead letter and replay was proven by S4 and is re-run by the broker epic (#7), and this epic offers its golden events as payloads for that suite, which #7 confirms in its own spec
- restart reconstruction tests per stage (proposal 10 of the epic), which belong to the failure scenarios epic (#15) and the stage epics, while this epic ships the poison vectors they use
- real stage code: the dispatch API, admission, the workflow, placement and the workers are other epics, and this epic proves its contract against fake stages
- the trace backend and its retention (#13), so the trace test uses an in-memory span exporter
- the CloudEvents Python SDK, binary mode, the batch format, and side-by-side emission of two majors, all ruled out above or by the epic
- any use of the cluster

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with `uv` and Python 3.13, `pytest` from `uv.lock`, and the recipes `just test`, `just itest [domain]` and `just ci` | `flake.lock`, `uv.lock`, workspace v1, recipe table v2 | none, exists [read: `scripts/test.sh`, `scripts/itest.sh`, `scripts/ci.sh`] |
| Dev environment | `deploy/versions.yaml` images map, where each entry is `{version, digest}` and a PR touches only its own keys | schema v1 | none, exists [read] |
| Dev environment | `scripts/lint.sh`, which runs `ruff` on every tracked `*.py` and `yamllint --strict` with a 120 column limit on every `*.yaml` | lint as in the repo | none, exists [read] |
| Spike S4 | The structured message shape on the wire: one AMQP data section of CloudEvents JSON, durable, empty application-properties, and content type `application/cloudevents+json` | `docs/spikes/broker-client.md` | the shape is the shape of the golden events, no stub |
| Spike S3 | Temporal 1.32.1 with Python SDK 1.34.0, and worker versioning on the workflow queue only | `docs/spikes/temporal.md` | none |
| Published CloudEvents 1.0.2 | `cloudevents.json` (JSON Schema, draft-07), the AMQP binding, the JSON format and the distributed tracing extension | tag `v1.0.2` of the specification repository [ran: all five files answered HTTP 200, and `cloudevents.json` is draft-07] | none |

The S4 messages used the content type `application/cloudevents+json; charset=UTF-8` and the epic writes `charset=utf-8`.
The profile pins the lower-case form the epic writes, and a reader compares the media type and the charset case-insensitively, so neither form is rejected.
The 2 MiB Temporal payload limit comes from the epic text [read], not from the spike.
No `revisit` child issue is open against the dev environment epic or the cluster epic [ran: `gh-axi issue list --label revisit --state all` shows only the closed recipe table issue].

### Produces (each pinned precisely)

Names, shapes and versions below are proposals that lane 0 and the lanes pin, and a change after the pin is a contract-change PR with a consumer list and a bump of the contract's `VERSION`.
For a generated schema that PR carries the contract file, the model under `packages/` and the regenerated schema together, because the drift check fails otherwise.
Epic text and notes link to these files and never copy them.

| To | What | Where it lives (path) | Shape (schema file, signature, exit codes, key names) | Version |
| --- | --- | --- | --- | --- |
| all epics | Events contract: the profile, one JSON Schema file per type and minor, vectors | `contracts/events/` | `README.md`, `VERSION`, `profile/` (profile schema and the vendored CloudEvents schema with its source commit and SHA-256), `schemas/<type without the dev.wdf prefix and the major>/<major>.<minor>.json`, `vectors/`. `check.sh <event.json>` exits 0 valid, 1 invalid with `reason: <code>: <detail>` on stderr, 2 usage. `dataschema` is `urn:wdf:schema:<type without prefix and major>:<major>.<minor>`, mapped to the schema file by the README. Reader rule: an unknown minor of a known major resolves to the highest minor the reader knows and added optional fields are ignored, while an unknown field is rejected and `dataschema` must map to a file only within a minor the reader knows. | events v1 |
| all epics | Event library: models, canonical serializer, size budget, store mapping, worker-side check | `packages/events/` (import `wdf_events`) | `to_bytes(event) -> bytes`, `from_bytes(data) -> Event`, `to_event_row(event) -> (row_key, event_time, body)` with `row_key` equal to `<source>#<id>` (the event id half of the event store key), while the CloudEvents `id` is never called an event id in code, `check_task_request(data) -> TaskRequest`. Typed errors `UnsupportedMajor`, `SchemaInvalid`, `EventTooLarge`, each with a `reason` string and a `dlq_reason` in the vocabulary of the broker epic. Schema generation `python -m wdf_events.schemagen [--check]`. | library v1 |
| all epics | Key derivation | `packages/keys/` (import `wdf_keys`) | `job_id(tenant, submission_key)`, `batch_id(tenant, batch_key)`, `workflow_id(job_id)`, `cloudevent_id(job_id, task_key, attempt_identity, kind, sequence)`, `attempt_identity(activity_attempt, resume_counter)`, `compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version)`. The formulas are in the README of `contracts/events/`, and `contracts/events/vectors/keys.json` holds only vectors. The workflow and the compile worker both import `compile_cache_key` and compute it nowhere else. | keys v1 |
| #3, #6, #8, #24 | Status contract | `contracts/status/` | `status.yaml` with the ten values `accepted, admitted, running, paused, resume-pending, succeeded, partial, failed, cancelled, terminated`, the terminal set (`succeeded, partial, failed, cancelled, terminated`), the two views `pausing` and `cancelling`, the projection from each job lifecycle event type to the status it sets (`resume.requested` sets `resume-pending`, `resumed` sets `running` when the workflow emits it and `accepted` when the `control` stage emits it for a job that never started, the other request types set none), and the allowed transition matrix. `README.md`, `VERSION`, `check.sh` and vectors. The guarded compare-and-set library stays in #3. | status v1 |
| #3, #10, #11, #12 | Artifact reference and bucket layout | `contracts/artifact/` | `reference.schema.json` (uri, sha256, size, name, kind, and the producer of job, task and attempt for attempt-scoped kinds, with no field that holds bytes), `layout.yaml` (the four buckets, the key template of each and the addressing class of each kind), `README.md`, `VERSION`, `check.sh` and vectors. #12 owns the semantics. | artifact v1 |
| #13 and every emitter | Instrumentation contract | `contracts/instrumentation/` | `README.md` with the rules, `VERSION`, and a `metrics.yaml` seeded with the metrics of this epic (question 4 of section 0.2) with its schema and a check. | instrumentation v1 |
| #6, #7, #8, #24 | Compatibility library | `packages/compat/` (import `wdf_compat`) | `read_event(data) -> Event` for the current and the previous major, one upgrade function per major step, `upgrade_for_replay(data) -> bytes`, and the window constants. | compat v1 |
| #7, #15 | Golden and poison vectors | `contracts/events/vectors/golden/`, `contracts/events/vectors/poison/` | One golden file per type and minor, and one poison file per failure, each with the typed error it must raise. | events v1 |
| #6, #10, #13 | Temporal helpers and the replay corpus | `packages/temporal/` (import `wdf_temporal`) | A payload converter that keeps the canonical event bytes in the history, a `traceparent` interceptor, `corpus/<release>/<name>.json` and a replay runner. Other epics add their own histories under the corpus rule of section 3. | temporal v1 |
| #3, #6, #8, #9, #10, #24 | Stage contract and the fake-stage runner | `contracts/stages/`, `packages/stages/` (import `wdf_stages`) | `stages.yaml` (per stage: source, attributes read, data fields read, events emitted) and `run_stage_contract(stage_name, stage_callable)`, which a real stage calls from one test in its own epic. | stages v1 |

### The batch manifest interface (shared with #5 and #24)

This is the interface that decision 9 records.
The validator epic (#5) and the dispatch epic (#24) consume it, and the storage epic (#3) holds the row.

- A batch carries one template manifest, stored by hash through #12 before any row is inserted, and content addressing dedups identical templates.
- Each job row holds its own seed, parameters, class override and request fingerprint, and these never travel in an event.
- The accepted event data holds the manifest reference of the template (an artifact reference of kind `manifest`), the batch id and the submission key.
- The plan of one job is its template plus its `job.params` block, which admission reads from the row and passes to the workflow start beside the accepted event (default of question 3 in section 0.2).
- #5 validates a template once per hash and the per-job parameters per job, and owns the content of both.
- A change to any line above is a contract-change PR on `contracts/events/` that lists #3, #5, #6, #8 and #24 as consumers.

## 3. Lanes (disjoint file ownership)

Package names are proposals.
Lane 0 owns the package it creates until its issues merge, then the owning lane takes over the glob, as the method says for stubs.

Departures from the method, stated so that the captain approves them knowingly:

- Lane Needs run across lanes: E.2 and E.3 feed lanes C, S and T, so lane 0 is not the only barrier.
  The reason is that no stub is possible for the catalogue before it exists, because a stub of its 24 types would be the catalogue.
  The sibling cluster spec has cross-lane Needs too (lane N after A.4, lane H after A.2, lane W after A.4).
- Contracts are pinned in lanes E, C and S as well as lane 0: the catalogue schemas in E, the golden and poison vectors in C, and the stage contract in S.
  Their content depends on the catalogue, and the method's tranche A for this epic does not name the stage contract.
- A contract-change PR for a generated schema is not `contracts/` only, because the model and the regenerated file must change with it (see section 2).
- No `just/<domain>.just` module is added by lane 0, because `just test`, `just itest <domain>` and `just ci` already run every test here.
- `docs/method.md` is not edited by this spec, and a follow-up edit is needed: its verifier table (line 220) and the tranche A row for E3 (line 245) still list a verifier skill for this epic, and its definition of done points at the exit test as "spec section 7", which is section 6 in this spec and in the cluster spec.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Contracts and skeleton | 1 | `contracts/events/{README.md,VERSION,check.sh}`, `contracts/events/profile/**`, `contracts/events/schemas/job.accepted/**`, `contracts/events/vectors/accepted/**`, `contracts/events/vectors/keys.json`, `contracts/status/**`, `contracts/artifact/**` (`reference.schema.json` is issue 0.1's and the rest is issue 0.2's), `contracts/instrumentation/**`, and until lanes E and K take over `packages/events/**`, `packages/keys/**` and `packages/itest/**` | 0.1, 0.2, 0.3, 0.4 | nothing (barrier) |
| E Events | 1 | `packages/events/**`, `contracts/events/schemas/**` for every type except `job.accepted`, `contracts/events/vectors/types/**` | E.1, E.2, E.3 | lane 0, and K.1 for E.2 |
| K Keys | 1 | `packages/keys/**`, and after lane 0 `packages/itest/**` (the lightest lane takes over the container helper) | K.1, K.2 | lane 0 |
| C Compatibility | 1 | `packages/compat/**`, `contracts/events/vectors/golden/**`, `contracts/events/vectors/poison/**` | C.1, C.2, C.3 | E.2, and E.3 for C.2 |
| T Temporal | 1 | `packages/temporal/**` | T.1, T.2, T.3 | E.3 and K.1 for T.1, and T.1 for T.2 and T.3 |
| S Stages | 1 | `packages/stages/**`, `contracts/stages/**` | S.1, S.2 | E.3 for S.1, and S.1 and K.1 for S.2 |

Shared-file rules:

- `uv.lock` is the one hotspot, and it is a shared file that every lane may touch, which the Done rule of section 4 allows.
  A PR that adds a package also commits the `uv lock` result, because `uv run --frozen` refuses an unlocked workspace member.
  A dependency lands in the PR that needs it (`pydantic` and `jsonschema` in 0.1, `hypothesis` in K.1, `psycopg` in E.2, `temporalio` and the OpenTelemetry packages in T.1 and T.2), and a lock conflict is never resolved by hand, `uv lock` is rerun after rebase.
- `deploy/versions.yaml` is shared with every epic and is likewise allowed by the Done rule.
  One key per line, and a PR touches only its own keys: lane E adds the Postgres image used by E.2, lane T adds the Temporal dev server image used by T.1.
- After lane 0 merges, a file of a pinned contract changes only through a contract-change PR.
  Lane E adds new `schemas/<type>/` directories under its own glob, and an edit to `schemas/job.accepted/` is a contract-change PR.
- `contracts/events/vectors/golden/` has a manifest of SHA-256 lines, one per golden file.
  A PR that changes a golden file or its line is a contract-change PR, and the test fails when a file and its line disagree or when a file has no line.
- `packages/temporal/corpus/` only grows, one directory per release, except for the documented retirement PR of T.3 that deletes a release directory after its window.
  Another epic adds its own recorded histories by its own PR under a `<release>/` directory it creates, and a line in `corpus/owners.yaml` that names the workflow type and its owner epic.
  Lane T owns the runner, which replays only the workflow classes it is given.
  It fails, with a message naming the history and its owner, on a history that has an `owners.yaml` entry and no class given to the runner, and on a history with neither, so a history is never skipped silently.
- The root `pyproject.toml` already declares `packages/*` as workspace members, so no lane edits it, and each package has its own `pyproject.toml` owned by its lane.
- `justfile`, `just/*.just`, `scripts/*` and `.github/workflows/*` are not touched, and the container helper of 0.4 is a package, not a script.
  No recipe is added, because `just test`, `just itest <domain>` and `just ci` already run every test below, and schema regeneration is `python -m wdf_events.schemagen`.
- The contract YAML files sit under `contracts/`, so `scripts/lint.sh` applies `yamllint --strict` to them and does not apply `kubeconform`, which only reads `deploy/`.
- Generated schema files are committed, and the drift check regenerates them, so a lane never hand edits a file under `schemas/`.

## 4. Issues

One block per issue, in the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md).
The blocks are replaced by `#NNN` links after filing, and each filed issue copies its Owns line from the lane table.
Each issue has a Test line naming its test files and tier.
When filed, the issue uses that line in the template section for the named test and keeps only the `just ci` and Owns checkboxes of Done means.
Done means, for every issue: `just ci` is green on the branch (it runs `just itest`, so every lane's gate needs Docker once the T1 tests exist, as section 7 says), the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files of section 3 changed.
Acceptance lines are commands with an observable result, except the README content lines, which the reviewer checks in the PR.
Proposal numbering from the epic is in brackets.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 CloudEvents profile and the accepted event v1 | 0.1 |
| 2 Event type catalogue v1 | E.2 |
| 3 Key derivation module | 0.1 (formulas, vectors, stub), K.1, K.2 |
| 4 Serializer size budget | E.1 |
| 5 Compatibility rules, golden events and the N-1 upgrade hook | C.1, C.2 |
| 6 Replay corpus | T.3 |
| 7 Trace context | T.2 |
| 8 Stage contract | S.1, S.2 |
| 9 Dead letter replay upgrader | C.3 (the function), the broker epic wires it at replay |
| 10 Restart reconstruction tests per stage | not delivered here, C.2 ships the poison vectors they use |
| 11 Task request event and result ready event | E.3 |
| not in the epic's list: Temporal handling of the structured body, a risk the epic names | T.1 |
| pulled forward by the method: contracts 2 and 3 and the instrumentation contract | 0.2, 0.3 |
| not in the epic's list: the T1 container helper that the integration tests share | 0.4 |

### Lane 0

**0.1 Events profile, accepted event and key vectors** [1, 3] Lane 0, tier T0. Needs: nothing.

- Scope: the profile rules of the epic as `contracts/events/`, the accepted event v1 as Python models with a generated schema, the vendored CloudEvents schema, `contracts/artifact/reference.schema.json` (the manifest reference of the accepted event validates against it), and the key formulas with vectors and a stub package.
- `uv run --frozen pytest packages/events/tests/test_profile.py -q` passes.
  Each file under `vectors/accepted/valid/` passes the vendored CloudEvents schema and the profile schema.
  Each file under `vectors/accepted/invalid/` fails with its own `reason`: missing `time`, `specversion` other than `1.0`, a type without a major suffix, a `time` that is not UTC with exactly six fractional digits and a `Z` suffix, a `subject` that is not the job id or `<job id>/<task id>` for the event's level, an extension name with a capital letter or over 20 characters, an `id` containing `#`, a `traceparent` that is not a valid W3C value, and a `dataschema` that is not a well formed identifier or names a major with no schema file.
  A `dataschema` of a known major with a minor the reader does not know is valid and resolves to the highest known minor, and a valid vector shows it.
  These vectors sit only under `vectors/accepted/`.
- `contracts/events/check.sh` exits 0 for each valid vector, 1 with `reason: <code>: <detail>` for each invalid one, and 2 without an argument.
- `uv run --frozen python -m wdf_events.schemagen --check` exits 0, and after a field is renamed in the accepted model it exits 1 and names `contracts/events/schemas/job.accepted/1.0.json` (the test `test_schema_drift.py` runs both cases).
- `to_bytes` and `from_bytes` for the accepted event, with the three typed errors, serialise the accepted vector to the same bytes twice and parse it back to an equal event, and its serialised size is under 2 KiB.
  `to_bytes` writes compact UTF-8 JSON with sorted keys, and E.1 extends the read path to every type.
- `contracts/events/vectors/keys.json` holds an input and expected output for every function of the key table, including pairs that differ only in where a field boundary falls.
  `uv run --frozen pytest packages/keys/tests/test_vectors.py -q` passes against the stub, whose functions raise `NotImplementedError("not implemented: owned by lane K")`.
  The test accepts that error only for the names in its `STUBBED` list, and a vector match for the rest.
  The formulas are written in the README of `contracts/events/` and nowhere else.
  The expected values come from them, computed by a scratch script that is not committed, and a vector that K.1 finds wrong is fixed by a contract-change PR.
- The README states the formulas, the reader rule for an unknown minor, the version, the owner epic, the consumers, the loader, the `time` rule (exactly six fractional digits, zero padded by producers, so that the event store key survives a `timestamptz` round trip), and the case-insensitive content type rule from section 2.
- Test: `packages/events/tests/test_profile.py`, `packages/events/tests/test_schema_drift.py`, `packages/keys/tests/test_vectors.py` (T0).

**0.2 Status and artifact reference contracts** [pulled forward] Lane 0, T0. Needs: 0.1. Question 2 and question 5 of section 0.2 apply.

- Scope: `contracts/status/` and `contracts/artifact/` as defined in section 2.
- `contracts/status/check.sh contracts/status/status.yaml` exits 0.
  Its invalid vectors exit 1 with a `reason`: a view stored as a status, a transition out of a terminal status, a job event type projected to two statuses without a `when` condition, and an unknown status.
- The projection maps each of the 16 job types (E.2 lists them) to a status or to none: `resume.requested` sets `resume-pending`, while the other three request types, `published` and `dead.lettered` set no status, and the row of a dead lettered job stays `accepted` so that a replay can admit it.
  `resumed` sets `running` when the workflow emits it.
  A job that never started is cancelled, terminated or paused on the row alone, so the `control` stage also emits `cancelled`, `terminated` and `paused` for it, and emits `resumed` when such a paused job is resumed, which returns its row to `accepted` because it was never running (S.1).
  The projection carries that `when` condition, and the transition matrix has the row-only pair `resume-pending` to `accepted` beside `accepted` to `paused`.
  `dead.lettered` is emitted only for a message that parses far enough to give a job id, and the broker dead letter queue is the only record of a message that does not.
- `contracts/artifact/check.sh` exits 0 on valid references and on keys that match their template, and exits 1 for a content-addressed key with the wrong hash prefix, an attempt-scoped key without an attempt segment, and a reference that has a field holding bytes.
  The `layout.yaml` buckets and key templates equal those of the storage epic (#3) [read: `wdf-sources`, `wdf-components`, `wdf-results`, `wdf-checkpoints`].
- Test: `contracts/status/check.sh` and `contracts/artifact/check.sh` over their vectors, run by `packages/events/tests/test_contract_vectors.py` (T0).

**0.3 Instrumentation contract** [pulled forward] Lane 0, T0. Needs: 0.1. Question 4 of section 0.2 applies.

- Scope: the rules and the seed `metrics.yaml` of the default in question 4.
- `contracts/instrumentation/README.md` states the metric naming rule, the label policy, the log field names and the trace conventions.
  The label policy is a denylist of unbounded identifiers (job id, task id, event id, trace id), plus the rule that every label declares its bounded set of values in `metrics.yaml`, as `reason`, `type` and `from_major` do, and the priority class and job class are the context attributes allowed as labels.
  The log field names reuse the context attribute names (`tenant`, `jobid`, `taskid`, `attempt`, `traceparent`) plus `event_type` and `cloudevent_id`, where `cloudevent_id` is the CloudEvents `id`, while the stored key keeps the name `event_id`.
- `metrics.yaml` holds `wdf_event_bytes`, `wdf_event_size_warnings_total`, `wdf_event_rejected_total` (label `reason`) and `wdf_event_upgraded_total` (labels `type` and `from_major`).
  `to_bytes` observes `wdf_event_bytes`, `to_bytes` raises the size warning and rejection counters, and `read_event` and `upgrade_for_replay` increment `wdf_event_upgraded_total` on each upgrade, and the tests of E.1, C.1 and C.3 assert each of these.
- `uv run --frozen pytest packages/events/tests/test_instrumentation.py -q` passes: the file validates against its schema, each name follows the rule, and each label declares its values and is not on the denylist, and a copy with a `jobid` label fails.
- Test: `packages/events/tests/test_instrumentation.py` (T0).

**0.4 Container helper for T1 tests** Lane 0, T1. Needs: nothing.

- Scope: `packages/itest/` (import `wdf_itest`), the one helper that starts the containers of T1 tests.
- It takes the worker slot from `WDF_SLOT` (default 0, slots 0 to 9), binds host ports `127.0.0.1:<19000 + slot * 10 + k>` with `k` below 10 as in rule 5 of the method, which keeps clear of `deploy/host/ports.yaml`, labels every container with the run label of that rule, and removes only containers carrying its own run label, including after a failed or interrupted test.
- It limits the host to two containers with two lock files, `/tmp/wdf-t1.0.lock` and `/tmp/wdf-t1.1.lock`.
  A test session that starts containers takes whichever file is free for its whole life, waits when both are held, and fails with a message naming the holders after a wait of 30 minutes.
  A session starts one container at a time and stops it before the next, so at most two T1 containers run on the host whatever the number of workers.
- It fails the test, and does not skip it, when Docker is absent.
- `uv run --frozen pytest packages/itest -m integration -q -rs` passes with at least one test: it starts the `canary` image of `deploy/versions.yaml` on a slot port, sees the label, interrupts a child test process, and finds no container with that run label afterwards.
  The child takes its own pair of lock files from a scratch directory that the test names in an environment variable, so interrupting it cannot deadlock on the real lock files.
- The same test file runs the helper with `DOCKER_HOST=unix:///nonexistent` and expects a failure, not a skip.
- Test: `packages/itest/tests/test_helper.py` (T1, the canary container).

### Lane E

**E.1 Serializer and size budget** [4] Lane E, T0. Needs: lane 0.

- Scope: the read path over every type, and the size budget, on top of the canonical writer of 0.1.
- `uv run --frozen pytest packages/events/tests/test_serializer.py -q` passes: `to_bytes(from_bytes(b)) == b` for every canonical vector and for events generated with a seeded stdlib `random` (no new dependency), and a non-ASCII field is counted in bytes and not in characters.
- An event of 16 KiB minus one byte serialises with no warning, an event of 16 KiB logs a warning and raises the counter `wdf_event_size_warnings_total`, an event of 64 KiB serialises, and an event of 64 KiB plus one byte raises `EventTooLarge` and raises `wdf_event_rejected_total{reason="oversized"}` (`test_budget.py`).
- `from_bytes` resolves `dataschema` through a registry of schema files, raises `UnsupportedMajor` for a type whose major it does not know before it opens the data, and `SchemaInvalid` for data that fails the schema named by `dataschema`.
  The reader rule of the profile applies: an unknown minor of a known major resolves to the highest known minor and ignores added optional fields, and an unknown field within a known minor is rejected.
  `to_bytes` observes `wdf_event_bytes` (`test_budget.py` asserts it).
- Test: `packages/events/tests/test_serializer.py`, `packages/events/tests/test_budget.py` (T0).

**E.2 Event type catalogue v1 and the store mapping** [2] Lane E, T0 and T1. Needs: E.1, K.1. Questions 1, 2 and 3 of section 0.2 apply.

- Scope: the schemas of the lifecycle types, the `job.params` block, the id and time rules, and `to_event_row`.
- The job types are `accepted`, `published`, `admitted`, `started`, `paused`, `pause.requested`, `resume.requested`, `resumed`, `cancel.requested`, `terminate.requested`, `succeeded`, `partial`, `failed`, `cancelled`, `terminated` and `dead.lettered`, under `dev.wdf.job.<name>.v1`.
  The task types are `placed`, `started`, `checkpoint.committed`, `attempt.failed`, `retries.exhausted`, `succeeded`, `failed` and `skipped`, under `dev.wdf.task.<name>.v1`.
  The data of `checkpoint.committed` carries a `sequence` number, which feeds the id.
  `job.dead.lettered` is built only when the message parses far enough to name a job.
  `job.accepted` is lane 0's, each type records the date its major was introduced for the window test of C.1, and the checkpoint committed event is the only checkpoint event of this epic.
- `uv run --frozen pytest packages/events/tests/test_catalogue.py -q` passes: every type has a schema directory, one valid and one invalid vector under `vectors/types/`, and a model, and `schemagen --check` is clean, with `job.accepted` the one exception whose vectors stay under `vectors/accepted/` only.
  The projection in `status.yaml` names only types that exist.
- A builder takes the facts of the event and calls `wdf_keys.cloudevent_id` for the `id`, so the id of a rebuilt event is equal, and `time` is an argument that the caller takes from the fact (`test_event_ids.py`).
  The time of the T1 case has the form `.123456Z`.
- `to_event_row(e)` returns `("<source>#<id>", time, bytes)`, equal for two builds of the same event, with an `id` that never holds `#` (`test_store_mapping.py`).
- T1: `uv run --frozen pytest packages/events/tests/integration -m integration -q` starts the pinned Postgres through `wdf_itest` on `127.0.0.1`, creates a scratch table with `UNIQUE (event_id, event_time)` and inserts one row twice with `ON CONFLICT DO NOTHING`.
  The second insert adds 0 rows, the `event_time` read back equals the event's `time` to the microsecond, and the container carries the run label and is gone after the run.
  The same test stores a body whose keys are not in sorted order as `text` and as `jsonb`, and asserts that `text` returns the bytes and `jsonb` does not, which is the fact the storage epic needs to choose the column type.
- Test: the files above (T0), and `packages/events/tests/integration/test_event_row.py` (T1, one Postgres container).

**E.3 Task request, result ready and the worker-side check** [11] Lane E, T0. Needs: E.2.

- Scope: the schemas of `dev.wdf.task.requested.v1` and `dev.wdf.task.result.ready.v1`, and `check_task_request`.
- The task request data holds the task type (the closed set `compile`, `simulate`, `aggregate`), the declared CPU and memory in the units #5 states, CPU cores and memory MiB (fields `cpu_cores` and `memory_mib`), input references, an optional resume checkpoint reference, and the deadline.
  Every reference validates against `contracts/artifact/reference.schema.json`.
  The result ready data holds one result reference of kind simulate result or aggregate summary.
- `uv run --frozen pytest packages/events/tests/test_task_request.py -q` passes.
  `check_task_request` returns a typed value for the valid vector and raises the named error for each invalid one: wrong major, missing CPU or memory, an unknown task type, a reference with inline bytes, and an unknown field within a known minor.
  A task request of a newer minor with one added optional field passes `check_task_request` and the field is ignored.
- A task request that lists 100,000 result references raises `EventTooLarge`, which is the guard on the aggregate input (the aggregate gets an index reference, #12).
- Test: `packages/events/tests/test_task_request.py`, `packages/events/tests/test_result_ready.py` (T0).

### Lane K

**K.1 Identity keys** [3] Lane K, T0. Needs: lane 0.

- Scope: job id, batch id, workflow id, the CloudEvents `id` and the attempt identity.
- `job_id` is a UUID version 5 over a length-delimited encoding of tenant and submission key in a fixed namespace, `batch_id` uses a different domain tag, and `workflow_id` is `job-<job id>`.
  `cloudevent_id` is built from job id, task key (empty for job events), attempt identity, kind and a `sequence`, with a character set that excludes `#`.
  `sequence` is 0 for a kind that occurs once per attempt, the control sequence of #24 for a control request and the events it causes, and the checkpoint sequence for `checkpoint.committed`.
- `uv run --frozen pytest packages/keys -q` passes with `hypothesis` property tests: deterministic across two interpreter runs with different `PYTHONHASHSEED`, distinct for `("ab", "c")` and `("a", "bc")`, distinct for a job key and a batch key that hold the same text, distinct across tenants, and never containing `#`.
- `test_attempt_restart_no_collision` builds ids and attempt segments for every pair of resume counter 0 to 3 and activity attempt 1 to 5, including the restart of the activity attempt to 1 after a resume, and finds 20 distinct values.
  A second case builds the ids of pause, resume, pause for one job with control sequences 1 to 3 and finds no repeated `(source, id)`.
  The resume counter is an argument, because it lives in workflow state (decision 10).
- The vectors of 0.1 match the real functions, the `STUBBED` list of `test_vectors.py` is empty, and the test fails if a function still raises the stub error.
- Test: `packages/keys/tests/test_vectors.py`, `packages/keys/tests/test_identity_properties.py` (T0).

**K.2 Compile cache key** [3] Lane K, T0. Needs: lane 0.

- Scope: `compile_cache_key`, one function over four inputs.
- `uv run --frozen pytest packages/keys/tests/test_compile_key.py -q` passes: the key changes when any one of source hash, lockfile hash, `componentize-py` version or WIT contract version changes, is equal for equal inputs in two interpreter runs, is distinct for `("1.2", "3")` and `("1", "2.3")`, and a hash that is not `sha256:` plus 64 hex digits raises.
- The package exports `compile_cache_key` once, and the README names it as the only place the key is computed.
  The workflow epic and the toolchain epic name the import in their own acceptance lines.
- Test: `packages/keys/tests/test_compile_key.py` (T0).

### Lane C

**C.1 Compatibility rules and the N-1 reader** [5] Lane C, T0. Needs: E.2.

- Scope: the evolution rules, `read_event`, the upgrade hook and the window.
- The README of the lane (under `packages/compat/`) states what a minor may change (an added optional field) and what needs a new major (a removal, a rename, a new required field, a narrowed type or enum).
- `uv run --frozen pytest packages/compat/tests/test_schema_evolution.py -q` passes on the catalogue and fails on two fixtures, a renamed field and a new required field, which are the two changes that wedged open workflows in the earlier replay test [read: epic risks].
- `uv run --frozen pytest packages/compat/tests/test_read_event.py -q` passes with a test-only type whose v1 and v2 schemas live under `packages/compat/tests/fixtures/` and are passed to `read_event` through a registry argument, and one upgrade function: v2 bytes read directly, v1 bytes read as v2 through the function, v3 bytes raise `UnsupportedMajor`, and v1 bytes raise it once the window constants retire v1.
  A vector with a newer minor than the reader knows and one added optional field parses, with the field ignored, which is the reader rule of the profile.
  `read_event` increments `wdf_event_upgraded_total` on an upgrade, and the test asserts it.
- `window.py` holds `MAX_JOB_DURATION_DAYS = 7` and `DEAD_LETTER_RETENTION_DAYS = 14` and a function that returns the days a previous major stays readable after its last producer, which is the dead letter retention plus the maximum job duration, 21 at the placeholders, because a message can be dead lettered on the last day, replayed on the last day of its retention and then start a job that runs for the maximum duration.
  The catalogue records the date each major was introduced, and `test_window.py` fails a catalogue that introduces a major within that many days of the previous one, so a reader never meets a major two steps back.
  Its fixtures are two small catalogues under `packages/compat/tests/fixtures/`, one with majors 20 days apart that must fail and one 22 days apart that must pass.
- Test: the three files above (T0).

**C.2 Golden events and poison vectors** [5, 10] Lane C, T0. Needs: E.2, E.3.

- Scope: one golden file per type and minor, a SHA-256 manifest, and the poison set.
- `uv run --frozen pytest packages/compat/tests/test_golden.py -q` passes: each golden file is rebuilt from fixed inputs to the same bytes, parses with `from_bytes`, and serialises back to the same bytes.
  The manifest check passes when every file and its line agree, and fails when a file has no line, a line has no file, or the hashes differ.
- `uv run --frozen pytest packages/compat/tests/test_poison.py -q` passes: each file under `vectors/poison/` makes `read_event` raise the error named in its sidecar file, for a wrong major, schema-invalid data, a missing `time`, an `id` with `#`, bytes that are not JSON, truncated JSON, a `datacontenttype` other than `application/json`, and an event over 64 KiB.
- Test: `packages/compat/tests/test_golden.py`, `packages/compat/tests/test_poison.py` (T0).

**C.3 Dead letter replay upgrader** [9] Lane C, T0. Needs: C.1.

- Scope: `upgrade_for_replay(data) -> bytes`, a pure function offered to the broker epic (#7) for the case where a replayed dead letter meets a newer reader.
  #7's text says replay republishes the original bytes, so whether replay calls the function or leaves the upgrade to the read path of admission is #7's call, confirmed in its spec, and `read_event` gives the same result either way.
- `uv run --frozen pytest packages/compat/tests/test_replay_upgrade.py -q` passes: an event of the previous major returns as canonical bytes of the current major with `id`, `source` and `time` unchanged, so the history row still dedups, an event of the current major returns byte-identical, an event two majors back raises `UnsupportedMajor`, and an invalid event raises `SchemaInvalid`.
  No call returns bytes that are partly upgraded.
  The window of C.1 assumes that a replayed dead letter is upgraded or read by a reader that still knows its major, so replay must call this function or the window covers the old major as C.1 writes it.
  `upgrade_for_replay` increments `wdf_event_upgraded_total` for an upgrade, and the test asserts it.
- Each typed error carries a `dlq_reason` in the vocabulary of #7 [read: `UNSUPPORTED_VERSION` for an unsupported major, `MALFORMED` for a schema-invalid or oversized event], and the README maps the `reason` strings to them.
- The broker side (publish, dead letter and replay on RabbitMQ) is not run here and is #7's regression suite.
- Test: `packages/compat/tests/test_replay_upgrade.py` (T0).

### Lane T

**T.1 CloudEvent through Temporal** [risk of the epic] Lane T, T1. Needs: E.3, K.1.

- Scope: a payload converter that keeps the canonical event bytes in the history, and a stand-in workflow for the later issues of this lane.
- The stand-in workflow takes the accepted event and a `job.params` block as its two start arguments (the default of question 3), runs one activity whose input is a task request event, and returns a result ready event.
  The Temporal dev server image is pinned by digest in `deploy/versions.yaml` and runs through `wdf_itest` on `127.0.0.1`.
- `uv run --frozen pytest packages/temporal/tests/test_payload.py -m integration -q` passes: the workflow input and the activity input payloads in the fetched Temporal history hold bytes equal to `to_bytes(event)`, and a replay of that history through `Replayer` succeeds.
  The payload form is a constant in the lane README, either `converter` (metadata encoding `json/plain`, so `json.loads` reads the data and the Temporal UI shows it) or `bytes`, and the test asserts that the recorded metadata matches the declared form.
  The container carries the run label and is gone after the run.
- If the converter cannot keep the bytes, the lane declares `bytes`, the UI then shows base64, and the PR says so for the captain.
  The corpus of T.3 uses whichever form is declared.
- Test: `packages/temporal/tests/test_payload.py` (T1, one Temporal container).

**T.2 Trace context** [7] Lane T, T0 and T1. Needs: T.1.

- Scope: `traceparent` created at submit, carried on events and through Temporal headers, and one span per stage.
- `uv run --frozen pytest packages/temporal/tests/test_traceparent.py -q` passes (T0): `new_traceparent()` returns a valid W3C `traceparent`, a malformed value is rejected by the profile, and `tracestate` is optional.
- `uv run --frozen pytest packages/temporal/tests/test_trace_spans.py -m integration -q` passes (T1): over the submit, admission, workflow and activity stand-ins with an in-memory exporter, every stage span has the trace id of the submit `traceparent`, is parented on the span of the stage before it, and the `traceparent` attribute of each emitted event has the same trace id.
  The test makes no claim about one trace over several days.
  The interceptor reuses `temporalio.contrib.opentelemetry` where it covers the case [read, not exercised].
- Test: the two files above.

**T.3 Replay corpus** [6] Lane T, T1 to record and T0 to replay. Needs: T.1.

- Scope: recorded histories per release, a replay gate, and the rules for adding and retiring entries.
- `uv run --frozen python -m wdf_temporal.corpus record <release>` (T1) writes `packages/temporal/corpus/<release>/<name>.json` from stand-in runs, each file with the build label that recorded it.
- `uv run --frozen pytest packages/temporal/tests/test_replay.py -q` passes (T0, no server): every history whose workflow type has a class replays against the current workflow code and payload decoding with `Replayer`, and the run fails, with a message naming the history and its owner, on a history that has an `owners.yaml` entry and no class given to the runner, and on a history with neither.
- `uv run --frozen pytest packages/temporal/tests/test_replay_negative.py -q` passes: a recorded history of a workflow whose event model has a renamed field makes the replay fail and name the history file, so the gate is shown able to fail.
- A release directory is retired by a PR that names it and deletes it after the window of C.1, which is the one exception to a corpus that only grows, and the README says so.
  Worker versioning for activity queues other than the workflow queue is unverified, as the epic says, and is not proven here.
- Test: `packages/temporal/tests/test_replay.py`, `packages/temporal/tests/test_replay_negative.py` (T0), and the record command (T1).

### Lane S

**S.1 Stage contract** [8] Lane S, T0. Needs: E.3. Question 1 of section 0.2 applies.

- Scope: `contracts/stages/stages.yaml` and its check.
- The stages are `dispatch` (source `/wdf/dispatch`, emits accepted and published), `control` (`/wdf/control`, emits the four request types, and `cancelled`, `terminated`, `paused` and `resumed` for a job that never started, each with a `when` field that names that condition), `admission` (`/wdf/admission`, emits admitted, and the dead lettered event for a message it rejects when the message parses far enough to name a job, and nothing for one that does not), `workflow` (`/wdf/workflow`, emits every other job and task type, and builds the task request without writing it to the event history), `placement` (reads the task type, CPU and memory of the task request, emits nothing) and `worker` (reads the task request, writes references into heartbeat details, emits nothing).
- `contracts/stages/check.sh contracts/stages/stages.yaml` exits 0, and exits 1 with a `reason` for a type emitted by two stages without a `when` field, a type other than the task request emitted by no stage, an emitted event whose `source` is not the stage's source, and an attribute or data field that does not exist in the catalogue.
- Obligations that the stage epics confirm in their own specs, because their texts do not list them yet: #24 emits `published` and `terminate.requested`, and emits `cancelled`, `terminated`, `paused` and `resumed` for a job with no workflow (a `resumed` there returns the row to `accepted`), while a dead letter made by the broker itself (delivery limit, expiry) has no emitter here and stays with the reporter question of #7, as does any message with no derivable job id.
- Test: `packages/stages/tests/test_stages_contract.py` over the file and its invalid vectors (T0).

**S.2 Fake-stage contract tests** [8] Lane S, T0. Needs: S.1, E.3, K.1.

- Scope: one fake per stage, a recording wrapper, and `run_stage_contract`.
- `uv run --frozen pytest packages/stages -q` passes: each fake reads only the attributes and data fields its stage declares and emits only declared types with the declared source, and a fake that reads an undeclared attribute fails (a negative control in the same file).
- A flow of one job with three tasks, one retry exhaustion, one resume and a second pause and resume pair runs through the fakes, produces no two events with the same `(source, id)`, and a second run of the same flow produces the same bytes, so a replay inserts no new `(row_key, event_time)` pair when the rows go through `to_event_row` into an in-memory set.
- `run_stage_contract(stage_name, stage_callable)` runs the same checks against any callable, and its README says that a stage epic adds one test that calls it with the real stage.
- Test: `packages/stages/tests/test_fake_stages.py`, `packages/stages/tests/test_flow.py` (T0).

## 5. Order, stubs and spikes

- Barrier: lane 0 (0.1, then 0.2, 0.3 and 0.4), one worker, merged alone.
  The estimate is two and a half days.
- After lane 0: E.1 starts with K.1 and K.2 (one worker for lane K), and none of them needs a container.
- DAG: lane 0, then (E.1 ‖ K.1, K.2), then E.2, then (E.3 ‖ C.1), then (C.2, C.3 ‖ S.1 ‖ T.1), then (S.2 ‖ T.2, T.3), then the exit test.
  E.2 waits for K.1 as well as E.1, C.2, S.1 and T.1 wait for E.3, T.1 also waits for K.1, and S.2 waits for S.1.
- Questions 1 to 3 of section 0.2 are answered before E.2 starts, question 4 before 0.3 and questions 2 and 5 before 0.2, or their defaults apply.
- T1 issues (0.4, E.2, T.1, T.2, T.3 recording) start containers through `wdf_itest`, which holds one of two lock files per session and starts one container at a time, so the containers on this host never exceed two whatever the number of workers.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| `packages/keys` functions in 0.1 | `NotImplementedError("not implemented: owned by lane K")` | `test_vectors.py` accepts that error only for the names in its `STUBBED` list, and a vector match for the rest, and K.1 makes it pass the vectors, so the stub and the real functions share one test |
| Stand-in workflow in `packages/temporal/` | the job workflow of #6, as three functions | #6 runs `run_stage_contract("workflow", ...)`, adds its own histories to the corpus with an `owners.yaml` entry, and replays them with its own class through the same runner |
| Fake stages in `packages/stages/` | dispatch, control, admission, workflow, placement and worker | the same contract runner runs against the real stage in its own epic, so a fake cannot drift silently |
| Scratch table in the E.2 Postgres test | the event table of #3 | the storage epic runs the golden event rows through its hypertable and expects 0 rows on the second insert, with the same two key columns |

Spikes: none.
S3, S4 and S5 are done and consumed above.
The one thing the epic names as unexercised that this epic can settle, Temporal handling of the structured body, is issue T.1 with a stated fallback, and no lane waits on a separate spike.

## 6. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main`, on this host with Docker running and no cluster started.
Each command runs from the export root as written, its real output is pasted in the PR that records the run, and it is summarised in Closure.
The steps that change a file do so in the scratch export and restore it afterwards.

| # | Command | Expected |
| --- | --- | --- |
| 1 | `nix develop -c uv sync --frozen` then `nix develop -c uv lock --check` | exit 0 for both, lock unchanged |
| 2 | `nix develop -c uv run --frozen python -m wdf_events.schemagen --check` | exit 0 with no drift |
| 3 | rename a field in the accepted model, run step 2 again, then restore the file | exit 1 naming `contracts/events/schemas/job.accepted/1.0.json` |
| 4 | `for f in contracts/events/vectors/accepted/valid/*.json; do nix develop -c contracts/events/check.sh "$f"; done`, then the same for `invalid/*.json` | every valid file exits 0, every invalid file exits 1 with a `reason:` line |
| 5 | `nix develop -c contracts/status/check.sh contracts/status/status.yaml`, `nix develop -c contracts/stages/check.sh contracts/stages/stages.yaml`, and each file under `contracts/artifact/vectors/valid/` and `invalid/` through `nix develop -c contracts/artifact/check.sh` | exit 0 for the two tables and the valid files, and exit 1 with a `reason:` line for each invalid file |
| 6 | `nix develop -c uv run --frozen pytest packages/keys -q -rs` | all pass, no `SKIPPED` line, and `test_attempt_restart_no_collision` is in the list |
| 7 | `nix develop -c uv run --frozen pytest packages/events packages/compat packages/stages -m "not integration" -q -rs` | all pass with no `SKIPPED` line, covering the size budget boundaries, golden events, poison vectors and the fake-stage flow |
| 8 | add a required field to an existing data model without a new major, then run `nix develop -c uv run --frozen pytest packages/compat/tests/test_schema_evolution.py -q`, then restore | the test fails and names the field |
| 9 | `nix develop -c uv run --frozen pytest packages/temporal -m "not integration" -q -rs` | the whole corpus replays, the negative control passes, no `SKIPPED` line |
| 10 | `nix develop -c uv run --frozen pytest packages/itest packages/events packages/temporal -m integration -q -rs`, then `docker ps -a --filter label=wdf.dev/verify-run` | all pass with at least one test collected in each package and no `SKIPPED` line, and the second command lists no container |
| 11 | `nix develop -c just ci` | exit 0, with the same integration tests run through `just itest` |
| 12 | the `ci` workflow on `main` | one green run, its URL recorded in Closure |

Not provable by this epic: the broker hop through RabbitMQ (#7 re-runs the S4 checks with the golden events), real stages emitting these events (each stage epic calls `run_stage_contract`), worker versioning on activity queues other than the workflow queue, a multi-day trace, restart reconstruction (#15) and any cluster behaviour.

## 7. Parallelism and cost

- Maximum parallel workers: 1 for lane 0, then 2, then 1, then 2, then 3 at the widest point (C.2 or C.3, S.1 and T.1 together), then 2.
- Host constraints: T0 work needs only `uv` and Python from the dev shell and has no limit.
  T1 work starts Postgres or the Temporal dev server by `docker run` on `127.0.0.1`, each capped at 1 GiB (S4 ran RabbitMQ and S3b ran Postgres at that cap, and the tests here are small), and the two lock files of `wdf_itest` keep at most two containers up on the 7.8 GiB host, while the cluster is never started.
  Their footprint was not measured for this epic.
- What each lane needs: lane K, C and S write only T0 tests, lane 0 needs Docker for the 0.4 helper test (T1), lane E needs Docker for the Postgres image in E.2 (T1), and lane T needs Docker for the Temporal dev server image and network access for `uv` to fetch `temporalio` and OpenTelemetry (T1).
  Every lane's Done gate runs `just ci`, which runs `just itest`, so once 0.4, E.2 and T.1 have merged every lane needs Docker for its gate, including lanes K, C and S, and a missing Docker daemon fails the gate.
  The T1 tests also run in CI on a runner with Docker.
- Estimates, not measurements: lane 0 (2.5 d), lane E (3.5 d, the longest), lane K (1.5 d), lane C (2 d), lane T (3 d, with a T1 flakiness risk), lane S (2 d), exit test (1 d).
  That is about 10 days elapsed along lane 0, E.1, E.2, E.3, T.1, T.3 and the exit test, and about 16 worker-days of effort, with `uv.lock` rebases and the T1 container runs as the main sources of slip.

## 8. Closure (filled at close)

<commit sha, CI run URL, exit test PR, what was learned, what moved, and the refinement of the architecture note>

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).
