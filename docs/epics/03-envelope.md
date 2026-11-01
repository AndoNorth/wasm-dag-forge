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
| all epics | Key derivation | `packages/keys/` (import `wdf_keys`) | `job_id(tenant, submission_key)`, `batch_id(tenant, batch_key)`, `workflow_id(job_id)`, `cloudevent_id(job_id, task_key, attempt_identity, kind, sequence)`, `attempt_identity(task_attempt, resume_counter)`, `compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version)`. The formulas are in the README of `contracts/events/`, and `contracts/events/vectors/keys.json` holds only vectors. The workflow and the compile worker both import `compile_cache_key` and compute it nowhere else. | keys v1 |
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
| 0 Contracts and skeleton | 1 | `contracts/events/{README.md,VERSION,check.sh}`, `contracts/events/profile/**`, `contracts/events/schemas/job.accepted/**`, `contracts/events/vectors/accepted/**`, `contracts/events/vectors/keys.json`, `contracts/status/**`, `contracts/artifact/**` (`reference.schema.json` is issue 0.1's and the rest is issue 0.2's), `contracts/instrumentation/**`, and until lanes E and K take over `packages/events/**`, `packages/keys/**` and `packages/itest/**` | [#87](https://github.com/AndoNorth/wasm-dag-forge/issues/87), [#88](https://github.com/AndoNorth/wasm-dag-forge/issues/88), [#89](https://github.com/AndoNorth/wasm-dag-forge/issues/89), [#90](https://github.com/AndoNorth/wasm-dag-forge/issues/90) | nothing (barrier) |
| E Events | 1 | `packages/events/**`, `contracts/events/schemas/**` for every type except `job.accepted`, `contracts/events/vectors/types/**` | [#93](https://github.com/AndoNorth/wasm-dag-forge/issues/93), [#94](https://github.com/AndoNorth/wasm-dag-forge/issues/94), [#95](https://github.com/AndoNorth/wasm-dag-forge/issues/95) | lane 0, and K.1 for E.2 |
| K Keys | 1 | `packages/keys/**`, and after lane 0 `packages/itest/**` (the lightest lane takes over the container helper) | [#91](https://github.com/AndoNorth/wasm-dag-forge/issues/91), [#92](https://github.com/AndoNorth/wasm-dag-forge/issues/92) | lane 0 |
| C Compatibility | 1 | `packages/compat/**`, `contracts/events/vectors/golden/**`, `contracts/events/vectors/poison/**` | [#96](https://github.com/AndoNorth/wasm-dag-forge/issues/96), [#97](https://github.com/AndoNorth/wasm-dag-forge/issues/97), [#98](https://github.com/AndoNorth/wasm-dag-forge/issues/98) | E.2, and E.3 for C.2 |
| T Temporal | 1 | `packages/temporal/**` | [#99](https://github.com/AndoNorth/wasm-dag-forge/issues/99), [#100](https://github.com/AndoNorth/wasm-dag-forge/issues/100), [#101](https://github.com/AndoNorth/wasm-dag-forge/issues/101) | E.3 and K.1 for T.1, and T.1 for T.2 and T.3 |
| S Stages | 1 | `packages/stages/**`, `contracts/stages/**` | [#102](https://github.com/AndoNorth/wasm-dag-forge/issues/102), [#103](https://github.com/AndoNorth/wasm-dag-forge/issues/103) | E.3 for S.1, and S.1 and K.1 for S.2 |

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

Each lane issue below was filed on 2026-10-30 from the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md), as a sub-issue of #4, and its body holds the scope, owns, acceptance criteria, verifier and needs.
Each issue copies its Owns line from the lane table, uses its Test line as the Verifier section, and keeps only the `just ci` and Owns checkboxes of Done means.
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

- [#87](https://github.com/AndoNorth/wasm-dag-forge/issues/87) **0.1 Events profile, accepted event and key vectors** [1, 3] Lane 0, tier T0. Needs: nothing.
- [#88](https://github.com/AndoNorth/wasm-dag-forge/issues/88) **0.2 Status and artifact reference contracts** [pulled forward] Lane 0, T0. Needs: 0.1. Question 2 and question 5 of section 0.2 apply.
- [#89](https://github.com/AndoNorth/wasm-dag-forge/issues/89) **0.3 Instrumentation contract** [pulled forward] Lane 0, T0. Needs: 0.1. Question 4 of section 0.2 applies.
- [#90](https://github.com/AndoNorth/wasm-dag-forge/issues/90) **0.4 Container helper for T1 tests** Lane 0, T1. Needs: nothing.
- [#93](https://github.com/AndoNorth/wasm-dag-forge/issues/93) **E.1 Serializer and size budget** [4] Lane E, T0. Needs: lane 0.
- [#94](https://github.com/AndoNorth/wasm-dag-forge/issues/94) **E.2 Event type catalogue v1 and the store mapping** [2] Lane E, T0 and T1. Needs: E.1, K.1. Questions 1, 2 and 3 of section 0.2 apply.
- [#95](https://github.com/AndoNorth/wasm-dag-forge/issues/95) **E.3 Task request, result ready and the worker-side check** [11] Lane E, T0. Needs: E.2.
- [#91](https://github.com/AndoNorth/wasm-dag-forge/issues/91) **K.1 Identity keys** [3] Lane K, T0. Needs: lane 0.
- [#92](https://github.com/AndoNorth/wasm-dag-forge/issues/92) **K.2 Compile cache key** [3] Lane K, T0. Needs: lane 0.
- [#96](https://github.com/AndoNorth/wasm-dag-forge/issues/96) **C.1 Compatibility rules and the N-1 reader** [5] Lane C, T0. Needs: E.2.
- [#97](https://github.com/AndoNorth/wasm-dag-forge/issues/97) **C.2 Golden events and poison vectors** [5, 10] Lane C, T0. Needs: E.2, E.3.
- [#98](https://github.com/AndoNorth/wasm-dag-forge/issues/98) **C.3 Dead letter replay upgrader** [9] Lane C, T0. Needs: C.1.
- [#99](https://github.com/AndoNorth/wasm-dag-forge/issues/99) **T.1 CloudEvent through Temporal** [risk of the epic] Lane T, T1. Needs: E.3, K.1.
- [#100](https://github.com/AndoNorth/wasm-dag-forge/issues/100) **T.2 Trace context** [7] Lane T, T0 and T1. Needs: T.1.
- [#101](https://github.com/AndoNorth/wasm-dag-forge/issues/101) **T.3 Replay corpus** [6] Lane T, T1 to record and T0 to replay. Needs: T.1.
- [#102](https://github.com/AndoNorth/wasm-dag-forge/issues/102) **S.1 Stage contract** [8] Lane S, T0. Needs: E.3. Question 1 of section 0.2 applies.
- [#103](https://github.com/AndoNorth/wasm-dag-forge/issues/103) **S.2 Fake-stage contract tests** [8] Lane S, T0. Needs: S.1, E.3, K.1.

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

Run on 2026-10-31 by a worker who did not write the lanes, on a clean `git archive` export of `origin/main` at `92c76e3`, the merge of the fake stages and the contract runner, which is the last lane merge.
Host: WSL2 with 20,002 MiB `MemTotal`, shared with 13 containers and a plain kind cluster of the captain that were running before and after and were not touched.
Docker was running and no cluster was started.
Real command output is in the pull request that records the run.

The row commands ran as written, from the export root, with the departures listed below.

| # | Result | Evidence |
| --- | --- | --- |
| 1 | pass | `uv sync --frozen` and `uv lock --check` exit 0, and the checksum of `uv.lock` is the same before and after |
| 2 | pass | `schemagen --check` exits 0 with no output |
| 3 | pass | renaming `submission_key` in `AcceptedData` gives `drift: contracts/events/schemas/job.accepted/1.0.json` and exit 1, and the restored file exits 0 |
| 4 | pass | the 3 valid files exit 0, and the 26 invalid files exit 1 with one `reason:` line each |
| 5 | pass | `status.yaml` and `stages.yaml` exit 0, the 11 valid artifact references exit 0, and the 14 invalid ones exit 1 with a `reason:` line |
| 6 | pass | 74 passed, no `SKIPPED` line, and `test_attempt_restart_no_collision` is collected |
| 7 | pass | 384 passed and 1 deselected, no `SKIPPED` line, with the budget, golden, poison and flow tests among them |
| 8 | pass | with `new_required: str` added to `AcceptedData` the test fails with `data.new_required: new required field`, 57 others pass, and the restored file passes all 58 |
| 9 | pass | 19 passed and 3 deselected, no `SKIPPED` line, the corpus replay and the negative control among them |
| 10 | pass | 11 passed in 14 s (7 in `packages/itest`, 1 in `packages/events`, 3 in `packages/temporal`), no `SKIPPED` line, and `docker ps -a --filter label=wdf.dev/verify-run` lists no container |
| 11 | pass | `just ci` exit 0, with 529 tests in `just test`, 11 integration tests in `just itest` and `nix flake check` passing, after the departure below |
| 12 | pass | `ci` run [41205020258](https://github.com/AndoNorth/wasm-dag-forge/actions/runs/41205020258) on `main` at `92c76e3`, completed with success |

Departures from the method:

- The first export sat under `/tmp`, and rows 1 to 10 ran there.
  Row 11 failed there with 6 failures out of 529 tests, all with `error: opening Git repository "/tmp"` from `nix shell --inputs-from` in `scripts/verify/cluster/lib/tools.sh`.
  An empty `/tmp/.git` directory had appeared at 13:29:21, and the process list shows a `bwrap` sandbox of another agent session on the host that mounts a tmpfs on `/tmp/.git`.
  The directory was gone again a few minutes later, so the cause is inferred from the process list and not proven.
  Row 11 was run again on a second export of the same commit under `~/.cache`, outside any git repository, and passed.
  Rows 1 to 10 were not run again on the second export.
- Row 5 lists the invalid artifact vectors by directory, one directory per reason, so the loop took every file under `invalid/`.
- Row 6 names no test in its output, so the presence of `test_attempt_restart_no_collision` was read from a separate `--collect-only` run.
  The same was done for the coverage named by row 7 and for the corpus and negative control of row 9, and for the packages of row 10.
- Row 12 needed no `workflow_dispatch`, because the run for the final lane merge was green.

Findings:

- `contracts/stages/check.sh` prints `RuntimeWarning: 'wdf_stages.check' found in sys.modules after import of package 'wdf_stages'` to stderr and still exits 0, because the package imports the module that `python -m` then runs.
- Every stale reference to the verifier and to "spec section 7" that section 3 named is still present in `docs/method.md` at lines 112, 220 and 245 on `92c76e3`.
- Row 10 ran with the default lock directory and slot 0 while no other T1 session was active, so the two-container limit and the lock wait were not exercised by this run.

Not proven:

- The broker hop through RabbitMQ, because #7 re-runs the S4 checks with the golden events, and whether the broker epic wires `upgrade_for_replay` at replay.
- Real stages emitting these events, because each stage epic calls `run_stage_contract`, and the read lists of admission, workflow and worker are first guesses that the stage epics confirm.
- Worker versioning on activity queues other than the workflow queue, a multi-day trace and restart reconstruction (#15).
- Any cluster behaviour.
- Replay across releases, because the corpus holds two stand-in histories of one release.
- The footprint of the T1 containers, which was not measured, and the host-wide limit of two containers under concurrent sessions.
- The Temporal behaviour of the 1.32.1 server of S3, because the lane recorded that its T1 test ran the embedded 1.32.0 server of a CLI image.
- A run with no cached container images, because the T1 images were present on the host.

Follow-ups recorded by the lanes and by this run, none of which blocks closure:

- Edit `docs/method.md` to drop the verifier rows for this epic (lines 220 and 245) and to point the exit test at section 6 (line 112), as section 3 recorded.
- Refine `docs/architecture/13-state-envelope-artifacts.drawio` with what the lanes learned, and set the `Status` line of this spec.
- Replace the placeholders of decision 12 (7 days and 14 days) and the single `INTRODUCED` date of the catalogue with real values, which changes `readable_days()` and may fail `check_catalogue`.
- Confirm in the specs of #6 and #8 that the workflow input is the accepted event plus the params block.
- Have the stage epics adapt real stages to the callable signature of `run_stage_contract`, supply the event sequence after a resume, and call `stage_span` themselves, because the workflow-side spans have zero duration.
- Have the observability epic take over `contracts/instrumentation/metrics.yaml`, with emitters that use its exact names and labels.
- Have the storage epic (#3) run the golden event rows through its hypertable and expect 0 rows on the second insert.
- Fix the `RuntimeWarning` of `contracts/stages/check.sh`.
- Treat the move matrix of the status contract as a first reading that the workflow epic may extend by a contract-change PR.
- Remove by label the container of a T1 session killed with SIGKILL, which cannot clean up after itself.
- Make `wdf_events.metrics` readable from a package installed outside the repository, which it is not because it reads the contract by repository path.

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).

- 2026-11-01: [#168](https://github.com/AndoNorth/wasm-dag-forge/issues/168) adds the `tmpfs`, `mounts` and `Container.exec` options to the container helper, all off by default.
