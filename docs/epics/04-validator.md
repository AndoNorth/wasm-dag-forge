# Epic #5: DAG configuration schema and validator - breakdown

Status: draft
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/5
Architecture note: docs/architecture/12-dag-and-workflow.drawio
Depends on (closed epics): Dev environment (#1, closed), Job event envelope (#4, closed)

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-10-31) while preparing this spec, `[read]` means read and not re-run.
The epic issue body was read on 2026-10-31, and this spec was written against that version.
`main` was at `b6e11ff`, the closure of the envelope epic.
Method: [docs/method.md](../method.md).
Models: [docs/epics/03-envelope.md](03-envelope.md) (the most recent, fully reviewed breakdown) and [docs/epics/02-cluster.md](02-cluster.md).
Spike results consulted: [Temporal](../spikes/temporal.md) (S3, S3b) for the 100,000 job scale, and [footprint](../spikes/S2-footprint.md) (S2) for the slot figures.
This epic has no cluster dependency, and its own tests need no container.
They run at tier T0 (host Python, no runtime), and the epic never starts a cluster or a container.
Only the `just ci` gate needs Docker, for the envelope integration tests that already exist on `main`.

## 0. Decisions for the captain (max 5)

### 0.1 Recorded decisions

Everything in the epic body stands.
The calls below were made for earlier epics, in the brief that asked for this spec, or (7 to 10) by the captain's answers to the four questions of the first draft, and are applied here without a new question.

| # | Decision | Source | Applied in |
| --- | --- | --- | --- |
| 1 | No verifier skill, no feature files and no verify recipes for this epic, and the `verify-job-model` row of the method is dropped. The product tests stay: the schema drift check, the validator fixtures, the property tests and the plan hash determinism test | the envelope epic, decision 3, carried | sections 3, 4 and 6 |
| 2 | Python models are the schema source, and the exported JSON Schema is checked in with a drift test | the envelope epic, decision 5, and the epic body | 0.1, 0.2 |
| 3 | `just` stays the entrypoint with logic in scripts. This epic adds no recipe, because `just test` and `just ci` already run every test below | the dev environment epic, carried | section 3 |
| 4 | A batch is one shared template manifest plus per-job parameters on the row. The interface is shared with the envelope and dispatch epics, and the envelope spec records it | the envelope epic, decision 9 | section 2, decision 7 |
| 5 | The first epic to need a container test helper already has one on `main` (`packages/itest/`, import `wdf_itest`). This epic needs no container, so it does not use it | the envelope epic, issue 0.4 | section 7 |
| 6 | Epic 4 is merged, so this spec consumes its real contracts, library names and versions as pinned in section 2 | the brief | section 2 |
| 7 | A batch binds its per-job values through placeholders in the template (option b of the former question 1), not through a fixed binding rule. The shape is narrow so that it stays data: a placeholder is the closed one-key object `{"param": "<name>"}`, it stands only as the whole value of an `args` key of a simulate or aggregate task or of the `seed` of a simulate task, and it never reaches a compile task. Substitution replaces that one value and never concatenates, formats, computes or branches. This departs from the epic body's non-goal of templating, and the departure is bounded by exactly that shape | the captain, 2026-10-31, on former question 1 | 0.3 (template parameters), sections 1, 2 and 4 (0.1, V.2, P.1, P.3, H.1, F.1) |
| 8 | The job class hint is derived from the critical path bound against a profile threshold of 3,600 s, over the classes `short` and `long`, and the submitter's override on the row wins | the captain, 2026-10-31, on former question 2 | 0.3 (job class), P.2 |
| 9 | The simulate slot comes from the slots files and not from a stand-in: a `revisit` child of the cluster epic (#2) adds a simulate entry to `deploy/presets/slots/{ci,dev,soak}.yaml`, as issue R.1 of a lane of its own, which merges before lane 0 starts | the captain, 2026-10-31, on former question 3 | section 3 (lane R), R.1, 0.2, section 5 |
| 10 | The entry module becomes a fifth input of `wdf_keys.compile_cache_key` and of its vectors, by the contract-change PR of issue 0.4, which merges with lane 0 before any consumer exists | the captain, 2026-10-31, on former question 4 | 0.4, section 2 (Consumes and the batch interface) |

### 0.2 Open questions

None.
The four questions of the first draft were answered by the captain on 2026-10-31 and are recorded as decisions 7 to 10 of section 0.1.

### 0.3 Proposals the lanes pin

These answer the open questions of the epic and the open design items it names.
Each is a default the lanes build on and the captain may change in the one review, and none needs an answer to start.
Names, numbers and shapes are pinned by lane 0 and change afterwards only through a contract-change PR.

| Item | Proposal | Epic question |
| --- | --- | --- |
| Template shape | The validator accepts any acyclic graph over the three types, typed by the registry, and the compile, simulate and aggregate chain is the documented shape and what the generator builds. A job with no aggregate is valid, because the smoke job is one level 0 chain. More than one aggregate task is valid, and none may consume an aggregate output. | 1, 9, 11 |
| Graph edges | `depends_on` lists control dependencies and every input reference adds a derived data edge, so the plan holds the union, sorted. A reference to a task that is not yet a dependency is not an error. | none |
| Limits numbers | The `ci` profile: 512 tasks, `max_parallel` 32 with a default of 8, plan at most 256 KiB of canonical bytes, document at most 1 MiB, 3 retries per task, 172,800 s per attempt, critical path at most 7 days (shared with `MAX_JOB_DURATION_DAYS` of `wdf_compat.window` by a drift test, and a bound along one path that does not count the waiting of a job wider than `max_parallel`, which `packages/plan/README.md` says), estimated history at most 10,000 events (Temporal warns at 10,240), and a job class threshold of 3,600 s over the classes `short` and `long`. Estimates of events: 10 per job, 8 per task and 6 per retry, so 512 tasks at 3 retries each reach 13,322 and the limit binds, until the workflow epic (#6) measures its shape. The estimate counts a first run and its retries and not pause, resume or patch cycles, so the history metric of #6 watches the real length. A `permissive` profile lifts every limit about a hundredfold for failure tests. | 5 |
| Compile defaults | The document never sets compile resources or timeout. The plan takes the compile slot budget, a timeout of 600 s and 1 retry from the profile. Aggregate retries default to 0. Simulate retries are required in the document. A plan is a function of the document, the job parameters and the limits profile, so it records the profile name, the `plan` command takes `--profile` with `ci` as its default, and the vectors hold plans under `ci`. A stored manifest never changes when a slots file changes, because it is compiled once at submit, and every plan task holds its resolved `cpu_milli` and `memory_mib`. The golden plans and the patch vectors are compiled against a fixed copy of the slots file, with a simulate entry, under `contracts/job/vectors/slots-fixture/`, so an edit of `deploy/presets/` never changes a golden byte. | none |
| Chains per job | No separate cap: the task count bounds chains, and a larger fan-in uses an aggregate-only job over an index object. | 2 |
| Batch sharing | A template is validated once per hash, and the per-job values per job, as in the recorded decisions 4 and 7. A job plan is the template plan with each placeholder replaced by the job's value. | 2 |
| Job class | Decision 8 names the rule: `class_hint(stats, profile)` returns `short` when the critical path bound is at most the profile threshold of 3,600 s and `long` above it. The submitter's override on the row is a value of the profile's `classes`, `short` and `long`, and it wins. | 3 |
| Priority class | Submission metadata only, never template content. | 4 |
| YAML | Not accepted, and the CLI does not convert it. | 6 |
| Existence checks | Inside a job, references are logical and need no check. The two content-addressed references that enter from outside, the source bundle and the aggregate index object, are checked by the caller through a resolver, and the content of the index object and its tenant ownership are checked by the toolchain epic (#11) and the artifact epic (#12), not here. | 7 |
| Runtime capabilities | No task declares any. The platform WIT contract version is the capability set, owned by #11. | 8 |
| Aggregation registry v1 | `summary_stats` with optional `blocks` and `columns` lists of strings, and `digest_list` with no arguments. Both read `result` outputs and write a `summary`. #11 confirms the argument names in its own spec. | 11 |
| Numbers | Documents hold integers, strings, booleans and lists of them, and no floats, so the canonical bytes need no float formatting. The one decimal is `cpu` in cores with at most 3 decimals, parsed as an exact decimal and stored as integer millicores. Seeds are integers below 2^53, so no JSON reader rounds one. | none |
| Template parameters | The document has an optional `parameters` map from a parameter name (pattern `^[a-z][a-z0-9_]{0,31}$`) to a type, one of `integer`, `string`, `boolean`, `seed` and `string-list`. A placeholder is the object `{"param": "<name>"}` and nothing else, and it may stand only as the whole value of a key of a simulate or aggregate task's `args` and as the whole value of the `seed` of a simulate task. Substitution replaces that one value with the job's value of that name, and never concatenates, formats, computes or branches, never fills a key, a list element, an id or a reference, and never reaches a compile task, so the compile tasks of a batch stay byte-identical. The type of a placeholder is the declared type of its parameter: `seed` for a seed field, the registry type of the argument for an aggregate argument (`string-list` for `blocks` and `columns`), and any declared type for a simulate argument, whose content the toolchain epic (#11) owns. The row's `seed` is the value of the reserved parameter `seed`, and the row's `parameters` map holds the others. Every declared parameter needs a value, a value for an undeclared name is rejected, and a template that does not declare `seed` ignores the row seed after its range check. The template plan keeps its placeholders and is what the manifest stores, and `bind` returns the plan with each placeholder replaced. There is no per-task seed derivation, so an author who wants distinct simulations in one job declares distinct seed parameters or writes literal seeds. This is data and not code because the marker is a closed one-key object, replacement is by whole value, and there is no operator that reads another value. | none |
| Other profiles | `dev` and `soak` use the `ci` numbers and differ only in the pool table read from their own slots file, with all three pools (compile, simulate and aggregate) read from it. `permissive` lifts the numbers about a hundredfold and the check ceilings that `pool_budget` returns to 1,000 cores and 1 TiB whatever the slots files say, while the compile defaults of a plan always come from the slot table as read, never from a lifted ceiling. | none |
| Patch rule | The table in issue H.1: frozen graph, parameter declarations, resources, timeouts, budgets and flags, parameters of not-yet-completed work (bundle, workload, arguments, seed and aggregation) changeable, completed simulate and aggregate tasks untouched, and a changed bundle re-runs the compile of the chain, refused when a simulate of that chain already completed. The chain of a compile task is the set of simulate tasks that name its `component` output in their `inputs`, and `depends_on` alone never puts a task in a chain. | 10 |
| Plan identity | `plan_hash(plan)` is the SHA-256 of `canonical_bytes(plan)`. Applied to the template plan it is the manifest hash, the address of the stored manifest, and applied to one bound job plan it is the job's plan hash. The CLI prints both under the keys `manifest_hash` and `plan_hash`, and `plan_hash` is `null` when no `--params` is given. | none |

### Resolutions of the review

The spec review of the first draft gave findings that needed a choice.
Each line below is the simplest default, applied in the text, and the captain can amend any of them.
They are not questions, and none blocks a lane.

- A placeholder never stands in a compile task, and one that does is `placeholder-in-compile` (alternative: allow it), because the compile tasks of a batch must stay byte-identical for the shared cache key.
- The row `seed` is the value of the reserved parameter `seed`, and there is no per-task seed derivation (alternative: derive one seed per task from the job seed and the task id), because a derivation is a computation that the placeholder rule rules out, and an author who wants distinct simulations declares distinct seed parameters.
- A template declares its parameters and their types in a `parameters` map (alternative: infer the type from the position of the placeholder), so a missing or unknown value is a plain check against a declaration.
- The simulate slot is 250 millicores and 384 MiB in the slots files (alternative: 500 millicores and 512 MiB, the figure of the first draft), because the first figure does not fit the simulate node of any preset under `scripts/cluster/slots.sh` (it leaves 300 millicores and 414 MiB free on `ci` after the daemons [ran: 500m and 512Mi prints `DOES NOT FIT`, 250m and 384Mi prints `fits` for `ci`, `dev` and `soak`]), and the entry stays an estimate until #10 characterizes the pool.
- A not yet completed checkpoint simulate whose `workload` or `args` change with the same seed is compatible and listed in `changed` (alternative: `frozen-field-changed`), because the patch rule changes parameters of unfinished work and #18 decides the resume.
- The seed list moves to `vectors/job-params/valid/` (alternative: keep it under `vectors/valid/` and filter by extension), so `vectors/valid/` holds only documents, and F.1 owns `job-params/valid/**` while P.3 owns `job-params/invalid/**`.
- `parse_document` takes `max_bytes`, and `validate` passes the profile's document limit (alternative: the size check moves into `validate`), so the loader stays the one place that rejects before parsing and 0.1 needs no profile.
- `critical_path_timeout_bound_s` and the class hint stay path bounds and the README says so (alternative: a width-aware duration bound along the lines of path plus the remaining work over `max_parallel`), because a duration bound is a new stat and limit, and #6 measures real durations.
- P.4 carries its own schema walker, tested on the shapes a task map takes (alternative: extend `wdf_compat`), because `wdf_compat` belongs to a closed epic.
- Golden plans and patch vectors compile against a fixed slots fixture, and `plan` takes `--slots-dir` (alternative: let the plan record a budget name), so an edit of `deploy/presets/` never changes a golden byte, and patch compares compile resources like every other field.
  The fixture holds a simulate entry and stays after R.1, so a change to the live slots files never changes a golden byte.
- R.1 merges before lane 0 starts (alternative: R.1 only before V.3, with `check.sh` reading the slots fixture or tolerating `SlotsIncomplete`), because `check.sh` loads the live slots files from lane 0 on, and a live file without a simulate entry would fail every input.
- The chain of a compile task is the simulate tasks that name its `component` output in their `inputs` (alternative: also those that list it in `depends_on`), because only data edges mean a result came from that build.
- `plan_hash` is `null` when `--params` is omitted (alternative: the hash of the template plan, which is `manifest_hash` already), so the two keys never hold the same value.
- Under `permissive` only the check ceilings are lifted, and compile defaults still come from the slot table (alternative: lift both), so a default compile task never asks for 1,000 cores.
- The spec has no verifier delta section, and the numbering note in section 3 says so (alternative: keep an empty section).
- `--resolver FILE` replaces the `--bundles DIR` tarball resolver, and `wdf_jobdoc.JobParams` is dropped, because each was a second implementation of something the double or the envelope model already does.
- `read_plan` reads version 1 and rejects any other, and the PR that introduces version N+1 adds the branch for N (alternative: a hook and a synthetic version 0 now).
- The library exports `canonical_bytes` and `plan_hash` only, and `manifest_hash` stays a JSON key of the CLI output (alternative: named functions for the manifest).
- The content of an aggregate index object and its tenant ownership are left to #11 and #12 (alternative: this epic reads the index through the resolver).

## 1. Goal and non-goals

Goal: the job definition and its validator.
A job is a JSON document built from the job template, tasks of exactly three types (compile, simulate, aggregate) with their dependencies, resource needs, timeouts and retry budgets, submitted with the Python source bundle that its compile task builds.
It is validated at the submit path, before the accepted-jobs row is written and before anything is published, and it compiles to a deterministic plan that the rest of the system consumes.
Configuration is data and never executable code.
One departure from the epic body is recorded (decision 7): its non-goals list templating, and this spec admits a single placeholder form, the closed object `{"param": "<name>"}` that stands as a whole value in simulate and aggregate arguments and in a simulate seed, is replaced by the job's value and never concatenates, computes or branches, and never reaches a compile task.
Nothing else in the document is templated.

Terms: the document (or template) is what an author writes, the plan is the validated, defaulted and ordered form, and a bound plan is a plan with one job's values substituted for its placeholders.
The manifest is the canonical bytes of the template plan, stored by hash.

Non-goals for this pass:

- everything the epic lists under "Does not own": what a task does when it runs (#11, #10), the submit sequence, reconciler, dispatch API and control API (#24), placement and queue choice (#9), intake queues (#7), the event and envelope schemas (#4), pool sizes and slot budgets (#2, #10), artifact storage and addressing (#3, #12), the checkpoint contract (#18), tenant, submission key and priority class, and expressions, conditionals, loops, dynamic task generation, any general workflow language and any templating beyond the placeholder of decision 7, which is the one departure from the epic body
- the real resolver of source bundles and index objects, which reads `wdf-sources` through the artifact client in the dispatch epic (#24), while this epic ships the interface and a test double that the CLI also loads from a file
- the bundle format, which the toolchain epic (#11) owns, so the library sees only a `BundleInfo`
- the content of an aggregate index object and the tenant that owns a bundle or an index object, which the toolchain epic (#11) and the artifact epic (#12) check, while this epic checks only that the reference exists
- the real pool table, which the workers epic (#10) owns, so the limits profile reads the slots files, which issue R.1 extends with a simulate entry marked as an estimate (decision 9)
- running the library inside a Temporal workflow sandbox, which the workflow epic (#6) proves with its own workflow, while this epic ships a static guard that keeps the library free of input and output and of clocks
- measuring the history event costs and the batch at the dispatch API, which belong to #6, #16 and the optional 100,000-row lane of #24
- YAML authoring, float parameters, runtime capability declarations and any use of the cluster or of a container

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with `uv` and Python 3.13, `pytest` and `hypothesis` from `uv.lock`, and the recipes `just test` and `just ci` | `flake.lock`, `uv.lock`, workspace v1, recipe table v2 | none, exists [ran: Python 3.13.15, pydantic 2.14.2, hypothesis 6.170.14, jsonschema 4.26.0, `just --list` prints 17 names] |
| Dev environment | `scripts/lint.sh`, which runs `ruff` on every tracked `*.py` and `yamllint --strict` with a 120 column limit on every `*.yaml` | lint as in the repo | none, exists [read] |
| Envelope | Events contract: the `urn:wdf:schema:<name>:<major>.<minor>` identifier style, the identifier style and the additive-only minor rule, which this epic follows for the document, while the reader differs on purpose: an event is replayed after a deploy and a submitted document is not, so a document minor above the validator's own is rejected | `contracts/events/` events v1 | none, exists [read: `contracts/events/README.md`] |
| Envelope | Artifact contract: the reference shape (`uri`, `sha256`, `size`, `name`, `kind`), the ten kinds, of which this epic uses `source`, `index`, `manifest`, `component`, `build-info`, `result` and `summary`, the bucket and key rule of `wdf-sources`, and the task id pattern `^[a-z0-9][a-z0-9._-]{0,62}$` of `producer.task` | `contracts/artifact/` artifact v1 | none, exists [read: `reference.schema.json`, `layout.yaml`] |
| Envelope | The per-job block `JobParams` (`seed`, `parameters`, `class_override`) that admission reads from the row and passes beside the accepted event | `wdf_events.models.JobParams`, library v1 | none, exists [read], this epic owns the content of `parameters`: `check_job_params` and `bind` take the block as a mapping and report its faults themselves, and callers parse the row with the envelope model, which has no upper bound on the seed and no type for `parameters` |
| Envelope | The task request data that a plan task maps to: `task_type`, `cpu_cores`, `memory_mib`, `inputs`, `resume_checkpoint` and `deadline`, built by the workflow from a plan task | `wdf_events.models.TaskRequestData`, library v1 | none, exists [read], a test of P.1 builds one from every plan task |
| Envelope | Compile cache key inputs: `wdf_keys.compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version)`, which has no entry module | `packages/keys/` keys v1, changed by issue 0.4 (decision 10) | none, exists [read] |
| Envelope | The placeholder `MAX_JOB_DURATION_DAYS` of 7, and `wdf_compat.breaking_changes(old, new)`, which walks only `properties` and `items` and so cannot see a task model held under `additionalProperties`, `$ref` or `oneOf`, which is why P.4 carries its own walker | `packages/compat/` compat v1 | none, exists [ran: both import, and the walk gap shown on hand-built schemas] |
| Envelope | The pool names `compile`, `simulate`, `aggregate`, `infra` and `obs` | `contracts/topology/topology.yaml` topology v1 | none, exists [read] |
| Cluster | Per-slot budgets of the compile and aggregate pools, one task per pod, marked `estimate: true`, with no simulate entry until issue R.1 adds one before lane 0 starts, and the slot check `scripts/cluster/slots.sh` with its tests `scripts/cluster/test_slots.py` | `deploy/presets/slots/<preset>.yaml` for `ci`, `dev` and `soak` at `b6e11ff`, and after R.1 with a simulate entry | none, R.1 merges before lane 0 starts |
| Envelope | The batch manifest interface: one template manifest per batch stored by hash, per-job parameters on the row, and the contract-change rule if it changes | `docs/epics/03-envelope.md` section 2 | none |
| Epic and Temporal | The 253 ms per 1,000 tasks validation measure [read: epic text], the 2 MiB Temporal payload limit [read: the envelope spec, from the epic text], and the 10,240 event history warning [read: Temporal documentation, not in the repo and not re-checked] | no pin, the spikes do not cover them | the estimate constants of 0.3 |

No `revisit` child issue is open against the dev environment, cluster or envelope epics [ran: `gh-axi issue list --label revisit --state all` shows only the closed recipe table issue].
The contract and package names below were checked free on `main` [ran: no `contracts/job/`, `packages/jobdoc/`, `packages/jobcheck/`, `packages/plan/`, `packages/jobpatch/`, `packages/jobtools/` or `packages/jobexamples/` exists].

### Produces (each pinned precisely)

Names, shapes and versions below are proposals that lane 0 pins, and a change after the pin is a contract-change PR with a consumer list and a bump of the contract `VERSION`.
For a generated schema that PR carries the contract file, the model under `packages/` and the regenerated schema together, because the drift check fails otherwise.
Epic text and notes link to these files and never copy them.

| To | What | Where it lives (path) | Shape (schema file, signature, exit codes, key names) | Version |
| --- | --- | --- | --- | --- |
| #24, #14, #15, #17, CI | Job contract: the document schema, plan schema, stats schema, registry, limits profile, error table and vectors | `contracts/job/` | `README.md`, `VERSION`, `schemas/job/1.0.json`, `schemas/plan/1.json`, `schemas/stats/1.json`, `registry.json`, `limits.yaml` with `limits.schema.json`, `errors.yaml` with `errors.schema.json`, `vectors/`. `check.sh <document.json>` runs the full validator with the `ci` profile and the resolver table `vectors/resolver.json` (a static map from SHA-256 to `BundleInfo`, plus the index objects that exist), and exits 0 valid, 1 invalid with one `reason: <code>: <detail>` line per issue on stderr, 2 usage. The document carries `schema: "urn:wdf:schema:job:1.0"`, and a reader accepts every minor up to its own and rejects a higher one with `unsupported-schema-version`. Without a resolver the library skips the bundle and index checks, which the CLI says in its output. | job v1 |
| all epics | Models, strict loader, registry, profile model: the document and task models (with the `parameters` declarations and the placeholder object), `parse_document(data: bytes, max_bytes: int) -> Document` (raises `DocumentInvalid` carrying `issues`), `Issue(code, path, message)`, `LimitsProfile`, `PoolTable` and the one loader that reads files, `load_profile(preset, root, slots_dir=None)` (`slots_dir` is passed by tests only, to read the slots fixture), which reads the compile, simulate and aggregate budgets from the slots file and raises `SlotsIncomplete` naming the pool when one is missing | `packages/jobdoc/` (import `wdf_jobdoc`) | The loader rejects duplicate keys, `NaN`, floats outside `cpu`, strings not in NFC or holding a lone surrogate, and bytes over `max_bytes` before it parses (`validate` passes the profile's document limit), and maps `RecursionError` and an integer over Python's digit limit to `not-json`, so no input ends in a traceback. Schema generation `python -m wdf_jobdoc.schemagen [--check]` writes and checks `schemas/job/`, `schemas/plan/`, `schemas/stats/`, `registry.json` and `limits.schema.json`. | library v1 |
| #24, #14 | Validator: `validate(data: bytes, profile, resolver=None) -> Report`, `Report(ok, issues, document)`, `Issue` re-exported from `wdf_jobdoc`, whose JSON Pointer `path` and `code` come from `errors.yaml`, `SourceResolver` with `resolve_source(ref) -> BundleInfo` and `exists(ref) -> bool`, `BundleInfo(source_sha256, lockfile_sha256, modules)`, `resolver_contract(resolver)`, and `pool_budget(profile, task_type)` | `packages/jobcheck/` (import `wdf_jobcheck`) | Pure and no I/O. Issues are sorted by path then code and capped at 100, so the same input gives the same answer. `pool_budget` is what the placement epic (#9) calls so that the unschedulable rule and the submit rule share one number. | validator v1 |
| #6, #8, #9, #24, #3 | Plan library: `compile_plan(document, profile) -> Plan`, `check_job_params(plan, params, profile) -> tuple[Issue, ...]` and `bind(plan, params, profile) -> Plan` (`params` is the row's parameter block as a mapping, and `bind` replaces each placeholder of the template plan by the job's value, which no placeholder of a compile task can ask for), `canonical_bytes(plan)`, `plan_hash(plan)`, `manifest_reference(plan) -> dict`, `task_request_fields(task) -> dict`, `stats(plan) -> PlanStats`, `class_hint(stats, profile) -> str`, `read_plan(data) -> Plan` (version 1, and `UnsupportedPlanVersion` for any other) | `packages/plan/` (import `wdf_plan`) | `Plan` holds `plan_version` (1), `profile` (the limits profile name it was compiled under), `max_parallel`, `parameters` (the declarations, kept after binding) and `tasks` in topological order with ties broken by task id, each with `id`, `type`, `pool`, `depends_on`, `inputs`, `cpu_milli`, `memory_mib`, `run_timeout_s`, `retries`, `checkpoint`, `allow_partial` and `params`, where `params` of a template plan may hold placeholders. Canonical bytes are `json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` encoded as ASCII, with integers only, and the loader has already rejected strings that are not in Unicode normalization form NFC or that hold a lone surrogate. `manifest_reference` takes the template plan and builds a reference of kind `manifest` from `canonical_bytes` and `plan_hash` that `contracts/artifact/check.sh` accepts. `PlanStats` has `task_count`, `counts`, `chain_count` (the number of compile tasks, each with the chain of simulate tasks that name its `component` output), `max_width`, `critical_path_timeout_bound_s` (a bound along one path, not a wall-clock bound), `peak_cpu_milli`, `peak_memory_mib`, `largest_cpu_milli`, `largest_memory_mib`, `checkpoint_tasks` and `estimated_history_events`. | plan v1 |
| #24, #6 | Patch check: `patch_check(old, new, completed) -> PatchReport` | `packages/jobpatch/` (import `wdf_jobpatch`) | `PatchReport(compatible, issues, changed, recompile)`. Pure over two bound plans and the set of completed task ids. | patch v1 |
| #14, #15, #17 | Example library, parameter vectors, golden plans and the generator | `packages/jobexamples/` (import `wdf_jobexamples`), `contracts/job/vectors/valid/`, `contracts/job/vectors/job-params/`, `contracts/job/vectors/plans/` | `examples() -> list[str]`, `load(name) -> bytes`, `generate(shape, chains, **kw) -> bytes` for `fan-out`, `fan-in` and `chains`, and `seed_list(count, start=0) -> bytes` (NDJSON, one job parameter block per line), all deterministic. The library reads the vectors by repository path, as `wdf_events.metrics` reads its contract. `vectors/valid/` holds only job documents (`<name>.json`), and parameter blocks and seed lists live under `vectors/job-params/valid/`. | examples v1 |
| #24, #14, CI | Validate and plan CLI | `packages/jobtools/` (import `wdf_jobtools`) | `python -m wdf_jobtools validate <document> [--profile ci\|dev\|soak\|permissive] [--resolver FILE] [--json]` (`FILE` is shaped like `vectors/resolver.json` and loaded through `StaticResolver`), `plan <document> [--profile P] [--slots-dir DIR] [--params FILE] [--out DIR] [--json]` (`--slots-dir` is passed through to `load_profile` and is how golden hashes are reproduced) and `patch-check <old> <new> --params FILE [--new-params FILE] --completed FILE`. Exit codes 0 ok, 1 invalid or incompatible, 2 usage or unreadable input. `--json` prints `{"ok", "issues": [{"code", "path", "message"}]}` for `validate` and `patch-check`, and for `plan` the same object plus `manifest_hash` (the plan hash of the template plan), `plan_hash`, `stats` and `class_hint`, where `plan_hash` is `null` when `--params` is omitted and `stats` and `class_hint` are those of the template plan. | cli v1 |

### The batch manifest interface (shared with #24 and #3)

This is the interface of the envelope spec, made concrete here.

- The template manifest of a batch is `canonical_bytes(plan)` of the template plan, and its address is the content-addressed reference `manifest_reference(plan)` builds.
- A job row holds its own seed, parameters and class override, validated by `check_job_params` and applied by `bind`: the seed is the value of the reserved parameter `seed`, the parameters fill the other placeholders of the template, and every declared parameter needs a value.
- The template manifest keeps its placeholders, so one manifest serves every job of the batch.
- The dispatch API (#24) calls `validate` once per template hash and `check_job_params` per job, then stores the manifest through the artifact client (#12) before any row is inserted.
- The compile tasks of every job of a batch are byte-identical after `bind`, so their cache key is one value, which the workflow computes with `wdf_keys.compile_cache_key` from `BundleInfo`, the entry module of the task and the toolchain pins of #11.
- A change to any line above is a contract-change PR that lists #3, #6, #8, #24 and the envelope contracts as consumers.

## 3. Lanes (disjoint file ownership)

Package names are proposals.
Lane 0 owns every package it creates until the owning lane takes over the glob, as the method says for stubs.

Departures from the method, stated so that the captain approves them knowingly:

- Lane Needs run across lanes: lane 0 waits for R.1 (which edits closed-epic files and has to land first, because `check.sh` loads the live slots files), V.3 waits for P.1 and P.2 (the validator checks the compiled plan, so `wdf_jobcheck` imports `wdf_plan` and never the reverse), H.1 for P.1 and P.3, F.1 for V.4, P.1 and P.3, F.2 for F.1, P.3 and H.1, P.4 for F.2, and the CLI lane for V.4, P.1 to P.3 and H.1.
  The reason is that a fixture, a golden plan or a CLI test over a stub would only assert `NotImplementedError`.
  The envelope spec records the same departure.
- Vectors are added under a class directory by the lane that owns the check, and golden plans by lane F, after lane 0 has pinned the contract.
  The error table is pinned whole in lane 0, so a lane adds fixtures and never codes.
- Issue 0.4 edits files of the closed envelope epic (the events contract README, its version, the key vectors and `packages/keys/`), as a contract-change PR with a consumer list, which the method allows for a pinned contract, and not as a `revisit` child.
- A contract-change PR for a generated schema is not `contracts/` only, because the model and the regenerated file must change with it.
- Issue R.1 is a `revisit` child of the closed cluster epic (decision 9).
  It edits files of that epic (the slots files, their test and one addendum line in the closure of its spec), as a contract-change style PR with a consumer list, and blocks V.3 as the method says.
- The root `pyproject.toml` is edited once, by lane 0 (issue 0.3), to list the new workspace members, because a plain `uv run` installs only what the dev group names.
- The verifier delta section of the template is absent because there is none (recorded decision 1), so the sections after section 5 are numbered one lower than in the template.
- No `just/<domain>.just` module is added, and `docs/method.md` is not edited by this spec.
  A follow-up edit is needed, because its verifier table (line 220) and the tranche A rows for E3 and E4 (lines 245 and 246) still list `verify-job-model`, and line 112 points the exit test at "spec section 7", which is section 6 here.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Contracts and skeleton | 1 | `contracts/job/{README.md,VERSION,check.sh,errors.yaml,errors.schema.json,limits.yaml,limits.schema.json,registry.json}`, `contracts/job/schemas/**`, `contracts/job/vectors/invalid/schema/**`, `contracts/job/vectors/slots-fixture/**`, `packages/jobdoc/**`, `contracts/events/{README.md,VERSION,vectors/keys.json}` and `packages/keys/**` for issue 0.4 only, the root `pyproject.toml` edit and `uv.lock`, and until lanes V, P, H, F and L take over `packages/jobdoc/**` (lane P), `packages/jobcheck/**`, `packages/plan/**`, `packages/jobpatch/**`, `packages/jobexamples/**` and `packages/jobtools/**` | [#125](https://github.com/AndoNorth/wasm-dag-forge/issues/125), [#126](https://github.com/AndoNorth/wasm-dag-forge/issues/126), [#127](https://github.com/AndoNorth/wasm-dag-forge/issues/127), [#128](https://github.com/AndoNorth/wasm-dag-forge/issues/128) | R.1 (barrier) |
| V Validation | 1 | `packages/jobcheck/**`, `contracts/job/vectors/invalid/{graph,reference,bundle,resource,limit}/**`, `contracts/job/vectors/resolver.json` | [#129](https://github.com/AndoNorth/wasm-dag-forge/issues/129), [#130](https://github.com/AndoNorth/wasm-dag-forge/issues/130), [#131](https://github.com/AndoNorth/wasm-dag-forge/issues/131), [#132](https://github.com/AndoNorth/wasm-dag-forge/issues/132) | lane 0, V.1, P.1 and P.2 for V.3, and V.1 to V.3 for V.4 |
| P Plan | 1 | `packages/plan/**`, `packages/jobdoc/**` after lane 0, `contracts/job/vectors/job-params/invalid/**` | [#133](https://github.com/AndoNorth/wasm-dag-forge/issues/133), [#134](https://github.com/AndoNorth/wasm-dag-forge/issues/134), [#135](https://github.com/AndoNorth/wasm-dag-forge/issues/135), [#136](https://github.com/AndoNorth/wasm-dag-forge/issues/136) | lane 0, P.1 for P.2 and P.3, and P.1 and F.2 for P.4 |
| H Patch | 1 | `packages/jobpatch/**`, `contracts/job/vectors/patch/**` | [#137](https://github.com/AndoNorth/wasm-dag-forge/issues/137) | P.1 and P.3 |
| F Fixtures | 1 | `packages/jobexamples/**`, `contracts/job/vectors/valid/**`, `contracts/job/vectors/job-params/valid/**`, `contracts/job/vectors/plans/**` | [#138](https://github.com/AndoNorth/wasm-dag-forge/issues/138), [#139](https://github.com/AndoNorth/wasm-dag-forge/issues/139) | V.4, P.1 and P.3 for F.1, and F.1, P.3 and H.1 for F.2 |
| R Revisit | 1 | `deploy/presets/slots/{ci,dev,soak}.yaml`, `scripts/cluster/test_slots.py`, `docs/epics/02-cluster.md` (one closure addendum line only) | [#124](https://github.com/AndoNorth/wasm-dag-forge/issues/124) | nothing (merges before lane 0) |
| L Tools | 1 | `packages/jobtools/**` | [#140](https://github.com/AndoNorth/wasm-dag-forge/issues/140), [#141](https://github.com/AndoNorth/wasm-dag-forge/issues/141) | V.4, P.1, P.2, P.3 and H.1 for L.1, and L.1 for L.2 |

Shared-file rules:

- `uv.lock` is the one hotspot, and a shared file that every lane may touch, which the Done rule of section 4 allows.
  Lane 0 commits the `uv lock` result for the new workspace members, because `uv run --frozen` refuses an unlocked member.
  No new third-party dependency is expected: `pydantic`, `jsonschema`, `pyyaml` and `hypothesis` are already locked, and a dependency lands in the PR that needs it.
  A lock conflict is never resolved by hand, `uv lock` is rerun after rebase.
- `deploy/versions.yaml`, `deploy/presets/*.yaml`, `justfile`, `just/*.just`, `scripts/*` and `.github/workflows/*` are not touched, except that R.1 alone edits `deploy/presets/slots/*.yaml` and `scripts/cluster/test_slots.py`.
  Every other lane reads the slots files and never edits them, because the cluster epic is closed.
- After lane 0 merges, a file of the pinned contract changes only through a contract-change PR, except the vectors that a lane adds under its own glob.
- Generated files (`schemas/`, `registry.json`, `limits.schema.json`) are committed, and the drift check regenerates them, so a lane never hand edits one.
- `contracts/job/vectors/plans/1/HASHES` has one SHA-256 line per golden file, as the golden events of the envelope contract do.
  A PR that changes a golden file or its line is a contract-change PR, and the test fails when a file and its line disagree or a file has no line.
- The contract YAML files sit under `contracts/`, so `scripts/lint.sh` applies `yamllint --strict` to them and does not apply `kubeconform`.
- Packages `jobdoc`, `jobcheck`, `plan` and `jobpatch` are pure: no input or output, clock, randomness, environment or network.
  Three tool modules read or write files and are exempt by name in the guard test of 0.3: `wdf_jobdoc.profile_files` (the one home of `load_profile`), `wdf_jobdoc.schemagen` and `wdf_jobcheck.check`, and no other module imports them.
  `jobtools` and `jobexamples` read files and are outside the guard.

## 4. Issues

Each lane issue below was filed on 2026-10-31 from the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md), as a sub-issue of #5 (R.1 as a `revisit` sub-issue of #2), and its body holds the scope, owns, acceptance criteria, verifier and needs.
Each issue copies its Owns line from the lane table, uses its Test line as the Verifier section, and keeps only the `just ci` and Owns checkboxes of Done means.
Done means, for every issue: `just ci` is green on the branch (it runs `just itest`, so every lane's gate needs Docker as the envelope spec says), the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files of section 3 changed.
Acceptance lines are commands with an observable result, except the README content lines and the lines that say a result is pasted in the PR or in Closure, which the reviewer checks in the PR.
Tiers are T0 throughout, and the proposal numbering of the epic is in brackets.
Commands are written without the `nix develop -c uv run --frozen` prefix that every one of them takes.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 Job template and task models | 0.1, 0.2 |
| 2 Graph validation | V.1 |
| 3 Artifact reference validation | V.2 |
| 4 Limits profile and resource sanity | 0.2 (profile, loader), R.1 (the simulate slot entry), V.3 (checks) |
| 5 Plan compiler and stats | P.1, P.2 |
| 6 Manifest storage contract | P.1 (bytes, hash, reference), confirmed by #3 and #12 in their specs |
| 7 Patch compatibility check | H.1 |
| 8 Validate and plan CLI | L.1 |
| 9 Example library, generator and invalid fixtures | F.1, F.2, and the class directories of lanes 0, V and H |
| 10 Versioning policy and compatibility tests | P.4 |
| 11 Batch validation performance | L.2 |
| not in the epic's list: per-job binding with placeholders, the validator entry point, the package skeletons, the cache key entry input, the simulate slot entry | P.3 (with 0.1 and V.2 for the placeholder checks), V.4, 0.3, 0.4, R.1 |

- [#125](https://github.com/AndoNorth/wasm-dag-forge/issues/125) **0.1 Document models, strict loader and error table** [1] Lane 0, tier T0. Needs: R.1.
- [#126](https://github.com/AndoNorth/wasm-dag-forge/issues/126) **0.2 Registries and the limits profile** [4] Lane 0, tier T0. Needs: 0.1. Decisions 8 and 9 of section 0.1 apply, and R.1 has merged, so the live slots files hold all three pools.
- [#127](https://github.com/AndoNorth/wasm-dag-forge/issues/127) **0.3 Plan model, package skeletons and stubs** Lane 0, tier T0. Needs: 0.2.
- [#128](https://github.com/AndoNorth/wasm-dag-forge/issues/128) **0.4 Compile cache key entry input (contract change)** Lane 0, tier T0. Needs: R.1. Decision 10 of section 0.1 applies. This issue merges with lane 0, so no consumer ever sees the four-input key.
- [#124](https://github.com/AndoNorth/wasm-dag-forge/issues/124) **R.1 Simulate entry in the slots files (revisit of the cluster epic)** [4] Lane R, tier T0. Needs: nothing. A `revisit` child of #2. It merges before lane 0 starts. Decision 9 of section 0.1 applies.
- [#129](https://github.com/AndoNorth/wasm-dag-forge/issues/129) **V.1 Graph validation** [2] Lane V, tier T0. Needs: lane 0.
- [#130](https://github.com/AndoNorth/wasm-dag-forge/issues/130) **V.2 Artifact reference and bundle validation** [3] Lane V, tier T0. Needs: lane 0.
- [#131](https://github.com/AndoNorth/wasm-dag-forge/issues/131) **V.3 Limits and resource sanity** [4] Lane V, tier T0. Needs: lane 0, V.1, P.1 and P.2.
- [#132](https://github.com/AndoNorth/wasm-dag-forge/issues/132) **V.4 The validate entry point** Lane V, tier T0. Needs: V.1, V.2 and V.3.
- [#133](https://github.com/AndoNorth/wasm-dag-forge/issues/133) **P.1 Plan compiler, canonical form and manifest** [5, 6] Lane P, tier T0. Needs: lane 0.
- [#134](https://github.com/AndoNorth/wasm-dag-forge/issues/134) **P.2 Plan stats and the class hint** [5] Lane P, tier T0. Needs: P.1. Decision 8 of section 0.1 applies.
- [#135](https://github.com/AndoNorth/wasm-dag-forge/issues/135) **P.3 Per-job values and binding** Lane P, tier T0. Needs: P.1. Decision 7 of section 0.1 applies.
- [#136](https://github.com/AndoNorth/wasm-dag-forge/issues/136) **P.4 Versioning policy and compatibility tests** [10] Lane P, tier T0. Needs: P.1 and F.2.
- [#137](https://github.com/AndoNorth/wasm-dag-forge/issues/137) **H.1 Patch compatibility check** [7] Lane H, tier T0. Needs: P.1 and P.3.
- [#138](https://github.com/AndoNorth/wasm-dag-forge/issues/138) **F.1 Example library and generator** [9] Lane F, tier T0. Needs: V.4, P.1 and P.3.
- [#139](https://github.com/AndoNorth/wasm-dag-forge/issues/139) **F.2 Golden plans and the fixture gate** [9] Lane F, tier T0. Needs: F.1, P.3 and H.1.
- [#140](https://github.com/AndoNorth/wasm-dag-forge/issues/140) **L.1 Validate and plan CLI** [8] Lane L, tier T0. Needs: V.4, P.1, P.2, P.3 and H.1.
- [#141](https://github.com/AndoNorth/wasm-dag-forge/issues/141) **L.2 Batch validation performance** [11] Lane L, tier T0. Needs: P.3 and L.1.

## 5. Order, stubs and spikes

- First: R.1, one worker, filed as a `revisit` child of #2 and merged alone before lane 0 starts, because `check.sh` of lane 0 loads the live slots files.
  It edits only closed-epic files, and the estimate is half a day.
- Barrier: lane 0 after R.1 (0.1, then 0.2, then 0.3, with 0.4 beside them), one worker, merged alone.
  The estimate is three days.
- After lane 0: V.1 and P.1 start together, each in its own lane, and none needs a container.
- DAG: R.1, then lane 0, then (V.1, V.2 ‖ P.1, then P.2 and P.3), then V.3 after P.2, then (V.4 ‖ H.1 after P.3), then (F.1 after V.4, P.1 and P.3, then F.2 after F.1, P.3 and H.1 ‖ L.1 after V.4, P.2, P.3 and H.1), then (P.4 after F.2 ‖ L.2 after L.1), then the exit test.
- All four questions of the first draft are answered (decisions 7 to 10), so no default is pending, and a later change to any of them is a contract-change PR.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| `validate`, `compile_plan`, `bind`, `patch_check`, `examples` and the CLI commands in 0.3 | `NotImplementedError("not implemented: owned by lane V")` and the same for lanes P, H, F and L | each package's `test_vectors.py` accepts that error only for the names in its `STUBBED` list and a real match for the rest, and the owning issue empties the list |
| `StaticResolver` | the resolver the dispatch epic (#24) builds on the artifact client | `resolver_contract(resolver)` runs against the double and, in #24, the real one, in one test of that epic |

Spikes: none.
The history event constants and the batch timing are estimates and a guard, recorded as such, and no lane waits on a measurement.

## 6. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main`, on this host with no cluster started, and with Docker running only for row 14.
Each command runs from the export root as written, its real output is pasted in the PR that records the run, and it is summarised in Closure.
The steps that change a file do so in the scratch export and restore it afterwards.
The export is of `main` after every lane has merged, in the order of section 5 with R.1 first, which rows 4, 5 and 16 depend on.

| # | Command | Expected |
| --- | --- | --- |
| 1 | `nix develop -c uv sync --frozen` then `nix develop -c uv lock --check` | exit 0 for both, lock unchanged |
| 2 | `nix develop -c uv run --frozen python -m wdf_jobdoc.schemagen --check` | exit 0 with no drift |
| 3 | rename a field in the compile parameter model, run step 2 again, then restore the file | exit 1 naming `contracts/job/schemas/job/1.0.json` |
| 4 | `for f in contracts/job/vectors/valid/*.json; do nix develop -c contracts/job/check.sh "$f"; done` | every file exits 0 |
| 5 | the loop of block A below | no output |
| 6 | `nix develop -c uv run --frozen pytest packages/jobdoc packages/jobcheck packages/plan packages/jobpatch packages/jobtools packages/jobexamples packages/keys -q -rs` | all pass with no `SKIPPED` line, covering the loader, limits, resolver contract, plan stats, binding, patch cases, golden plans and the fixture gate |
| 7 | `nix develop -c uv run --frozen pytest packages/jobcheck packages/plan -q -rs -k property` | all pass, and the list includes `test_cycle_path_is_a_real_cycle`, `test_acyclic_documents_are_accepted`, `test_hash_ignores_key_order_and_whitespace` and `test_order_is_topological_and_stable` |
| 8 | the commands of block B below | prints `same`, and the `plan_hash` of the JSON it then prints is the SHA-256 on the `level2-aggregate.seed-7.plan.json` line of `contracts/job/vectors/plans/1/HASHES` |
| 9 | `nix develop -c uv run --frozen python -m wdf_jobtools validate contracts/job/vectors/invalid/graph/dependency-cycle/three-task-cycle.json --json` | exit 1, one `dependency-cycle` issue with a JSON Pointer `path` |
| 10 | the commands of block C below | exit 0 with `recompile` listing the compile task, then exit 1 with `build-changed-after-result` |
| 11 | add a required field to the document model without a new version, run `nix develop -c uv run --frozen pytest packages/plan/tests/test_schema_evolution.py -q`, then restore | the test fails and names the field |
| 12 | `nix develop -c uv run --frozen pytest packages/jobdoc packages/jobcheck packages/plan packages/jobpatch -q -rs -k "no_io_imports"` | all pass, none skipped |
| 13 | `nix develop -c uv run --frozen python -m wdf_jobtools.bench --jobs 100000` | exit 0, `template_validations: 1`, `jobs: 100000`, `seconds` below 60 |
| 14 | `nix develop -c just ci` | exit 0 |
| 15 | the `ci` workflow on `main` | one green run, its URL recorded in Closure |
| 16 | `for p in ci dev soak; do nix develop -c scripts/cluster/slots.sh $p \| grep '^slot simulate:'; done` | three lines, each naming `250m/384Mi` and ending `fits` |

Block A, row 5:

```sh
for f in contracts/job/vectors/invalid/*/*/*.json; do
  want=$(basename "$(dirname "$f")")
  nix develop -c contracts/job/check.sh "$f" 2>&1 | grep -q "^reason: $want:" || echo "MISMATCH $f"
done
```

Block B, row 8:

```sh
doc=contracts/job/vectors/valid/level2-aggregate.json
params=contracts/job/vectors/job-params/valid/level2-aggregate.seed-7.json
slots=contracts/job/vectors/slots-fixture
a=$(nix develop -c uv run --frozen python -m wdf_jobtools plan "$doc" --slots-dir "$slots" --params "$params" --json)
b=$(nix develop -c uv run --frozen python -m wdf_jobtools plan "$doc" --slots-dir "$slots" --params "$params" --json)
[ "$a" = "$b" ] && echo same
echo "$a"
```

Block C, row 10:

```sh
for c in ok/bundle-changed build-changed-after-result/simulate-completed; do
  d=contracts/job/vectors/patch/$c
  nix develop -c uv run --frozen python -m wdf_jobtools patch-check "$d/old.json" "$d/new.json" \
    --params "$d/params.json" --completed "$d/completed.json"; echo "exit $?"
done
```

Not provable by this epic: the wall-clock duration of a job wider than `max_parallel` (the limit is a path bound), the content and tenant ownership of an aggregate index object (#11 and #12), the library inside a real Temporal workflow sandbox and the replay of old golden plans by a workflow (#6), the real resolver against the object store and the 100,000 job call through the API (#24 and #12), the bundle format (#11), the real pool table (#10 and #9), the history event constants against a real workflow (#6 and #16), and any cluster behaviour.

## 7. Parallelism and cost

- Maximum parallel workers: 1 for R.1, then 1 for lane 0, then 2 (V.1 and P.1), then 3 at the widest point (V, P and H), then 2 (F.2 beside L.1, then P.4 beside L.1 or L.2), then 1 for the exit test.
- Host constraints: every issue is T0 and needs only `uv` and Python from the dev shell, so there is no limit on a worker beyond CPU.
  Every lane's Done gate runs `just ci`, which runs `just itest`, so each gate needs Docker for the envelope integration tests that already exist on `main`.
  The new packages start no container.
- Estimates, not measurements: lane 0 (3 d), lane V (3.5 d), lane P (3.5 d, P.4 waits for F.2), lane H (1.5 d), lane F (2 d), lane L (2 d), lane R (0.5 d, before lane 0), exit test (1 d).
  That is about 10.5 days elapsed along R.1, lane 0, V.1 to V.4, F.1, F.2, P.4 and the exit test, and about 16.5 worker-days of effort, with `uv.lock` rebases and the cross-lane Needs as the main sources of slip.

## 8. Closure (filled at close)

Closed 2026-11-01 at `c9e8f96b3910b41c3413b71278f6551e992b4e74`, the commit of the last lane merge (P.4).
All sixteen rows of the exit test of section 6 pass.
The run is recorded with its pasted output in the PR that adds this section.

Method of the run:

- The export is `git archive origin/main` extracted into a scratch directory outside every git repository, and no row ran from a worktree.
- The worker that ran it wrote none of the lanes.
- Docker was running for row 14 only, and no cluster was started.
- The containers of the host that were already running (a plain kind cluster among them) were not touched, and the set of containers was identical before and after row 14.
- Rows 3 and 11 edited one line of `packages/jobdoc/src/wdf_jobdoc/models.py` in the export, and the file was restored from a copy and checked by SHA-256 each time.

Results:

| # | Result | Evidence |
| --- | --- | --- |
| 1 | pass | `uv sync --frozen` exit 0, `uv lock --check` exit 0 with 42 packages resolved and no change |
| 2 | pass | `schemagen --check` exit 0, no output |
| 3 | pass | renaming `CompileParams.entry` gave exit 1 with `drift: contracts/job/schemas/job/1.0.json` and `drift: contracts/job/schemas/plan/1.json`, and the check is exit 0 again after the restore |
| 4 | pass | 7 valid vectors, every one exit 0 |
| 5 | pass | 91 invalid vectors, block A printed nothing |
| 6 | pass | 881 passed, no `SKIPPED` line |
| 7 | pass | 4 passed, and the four named tests are in the list |
| 8 | pass | `same`, and `plan_hash` `0bc56175...5fa4fd4` equals the `HASHES` line of `level2-aggregate.seed-7.plan.json` |
| 9 | pass | exit 1, one `dependency-cycle` issue at `/tasks/a` |
| 10 | pass | exit 0 with `recompile: build`, then exit 1 with `build-changed-after-result` |
| 11 | pass | adding a required `owner` field to `Document` failed `test_evolution_committed_schema_is_additive_over_the_models[job-1.0]` with `job/1.0.owner: new required field`, and the file was restored |
| 12 | pass | 1 passed, none skipped |
| 13 | pass | `template_validations: 1`, `tasks: 8`, `jobs: 100000`, `seconds: 23.49`, exit 0 |
| 14 | pass | exit 0 in 5 min 28 s: lint, 1,336 unit tests passed, 11 integration tests passed, `nix flake check` passed, and no container labelled `wdf.dev/verify-run` remained |
| 15 | pass | https://github.com/AndoNorth/wasm-dag-forge/actions/runs/41302655498, workflow `ci` on `main` at `c9e8f96`, conclusion success, so no `workflow_dispatch` was needed |
| 16 | pass | `250m/384Mi` and `fits` for `ci`, `dev` and `soak` |

Departures from the method:

- The first export went under the firstmate data directory, which sits inside another git repository, where `nix develop` sees only tracked files.
  The export was moved to a directory with no enclosing repository, so the flake is read as a plain path and the commands run as written.
- Row 6 was typed once with a quoting error in the harness, which ran nothing, and the same command was then run as written.
- The names of row 7 come from a second run of the same selection with `-v`, since `-q` does not list them.
- Row 12 selects one test, `test_no_io_imports`, and that one test walks the sources of all four packages.
- Row 3 names the plan schema as well as the job schema, and the row expects only the job schema.
- The loop of row 4 printed the exit status of each file, which the row does not show.

Not proven by this epic (the list of section 6 stands, and nothing below was seen to contradict it):

- the wall-clock duration of a job wider than `max_parallel`, because the limit is a path bound
- the content and tenant ownership of an aggregate index object, the bundle format and the real resolver against the object store
- the library inside a real Temporal workflow sandbox, and the replay of old golden plans by a workflow
- the 100,000 job call through the dispatch API, since row 13 measures the library on one WSL2 host and not an API call
- the real pool table, the history event constants against a real workflow, and any cluster behaviour

Seen during the run and not covered by a row:

- Row 13 includes hashing every bound plan, which the lane recorded as about 15 s per 1,000 jobs at 512 tasks, so whether dispatch hashes each bound plan or only the manifest stays open.
- The 60 s guard of row 13 is a regression check on one host, and a much slower runner could trip it.
- Two lanes reported that `just ci` hit `port is already allocated` on `127.0.0.1:19000` from a container that was not theirs and passed on a rerun.
  The run recorded here did not hit it.

Follow-ups recorded by the lanes:

- `docs/method.md` still lists `verify-job-model` for the envelope and validator epics (lines 220, 245 and 246) and points the exit test at "spec section 7" (line 112), where this spec numbers it section 6, as section 3 of this spec says.
- The workload names `echo` and `pi` and the `steps` parameter of the example vectors are placeholders until the toolchain epic fixes its workload library.
- The pointer of a problem in an argument list names the list and not the element, which a later CLI may refine.
- The codes `placeholder-misplaced` and the other additions of the document models are cheap to rename only until a consumer pins them.
- Whether a paused checkpoint task resumes under changed code is left to the checkpoint contract, and `changed` of the patch report is the list it consumes.
- `patch-check --json` gained two keys beyond the pinned shape, and a strict consumer would have to allow them.
- `check.sh` reads the live `ci` slots, so an edit of `deploy/presets/` changes what the command line accepts, while the golden plans stay on the slots fixture.
- The `read_plan` reader knows version 1 only, and the PR that introduces version 2 adds the branch for 1.

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).
