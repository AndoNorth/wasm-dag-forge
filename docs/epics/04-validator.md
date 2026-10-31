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
| 9 | The simulate slot comes from the slots files and not from a stand-in: a `revisit` child of the cluster epic (#2) adds a simulate entry to `deploy/presets/slots/{ci,dev,soak}.yaml`, as issue R.1 of a lane of its own, and V.3 waits for it | the captain, 2026-10-31, on former question 3 | section 3 (lane R), R.1, 0.2, V.3, section 5 |
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
  The fixture holds a simulate entry, so lane 0 tests the pool table without waiting for R.1, and only the tests of the live slots files wait.
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
| Cluster | Per-slot budgets of the compile and aggregate pools, one task per pod, marked `estimate: true`, with no simulate entry until issue R.1 adds one, and the slot check `scripts/cluster/slots.sh` with its tests `scripts/cluster/test_slots.py` | `deploy/presets/slots/<preset>.yaml` for `ci`, `dev` and `soak` at `b6e11ff`, and after R.1 with a simulate entry | the slots fixture of 0.2, which holds a simulate entry, until R.1 merges |
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

- Lane Needs run across lanes: V.3 waits for P.1, P.2 and R.1 (the validator checks the compiled plan, so `wdf_jobcheck` imports `wdf_plan` and never the reverse), H.1 for P.1 and P.3, F.1 for V.4, P.1 and P.3, F.2 for F.1, P.3 and H.1, P.4 for F.2, and the CLI lane for V.4, P.1 to P.3 and H.1.
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
| 0 Contracts and skeleton | 1 | `contracts/job/{README.md,VERSION,check.sh,errors.yaml,errors.schema.json,limits.yaml,limits.schema.json,registry.json}`, `contracts/job/schemas/**`, `contracts/job/vectors/invalid/schema/**`, `contracts/job/vectors/slots-fixture/**`, `packages/jobdoc/**`, `contracts/events/{README.md,VERSION,vectors/keys.json}` and `packages/keys/**` for issue 0.4 only, the root `pyproject.toml` edit and `uv.lock`, and until lanes V, P, H, F and L take over `packages/jobdoc/**` (lane P), `packages/jobcheck/**`, `packages/plan/**`, `packages/jobpatch/**`, `packages/jobexamples/**` and `packages/jobtools/**` | 0.1, 0.2, 0.3, 0.4 | nothing (barrier) |
| V Validation | 1 | `packages/jobcheck/**`, `contracts/job/vectors/invalid/{graph,reference,bundle,resource,limit}/**`, `contracts/job/vectors/resolver.json` | V.1, V.2, V.3, V.4 | lane 0, V.1, P.1, P.2 and R.1 for V.3, and V.1 to V.3 for V.4 |
| P Plan | 1 | `packages/plan/**`, `packages/jobdoc/**` after lane 0, `contracts/job/vectors/job-params/invalid/**` | P.1, P.2, P.3, P.4 | lane 0, P.1 for P.2 and P.3, and P.1 and F.2 for P.4 |
| H Patch | 1 | `packages/jobpatch/**`, `contracts/job/vectors/patch/**` | H.1 | P.1 and P.3 |
| F Fixtures | 1 | `packages/jobexamples/**`, `contracts/job/vectors/valid/**`, `contracts/job/vectors/job-params/valid/**`, `contracts/job/vectors/plans/**` | F.1, F.2 | V.4, P.1 and P.3 for F.1, and F.1, P.3 and H.1 for F.2 |
| R Revisit | 1 | `deploy/presets/slots/{ci,dev,soak}.yaml`, `scripts/cluster/test_slots.py`, `docs/epics/02-cluster.md` (one closure addendum line only) | R.1 | nothing |
| L Tools | 1 | `packages/jobtools/**` | L.1, L.2 | V.4, P.1, P.2, P.3 and H.1 for L.1, and L.1 for L.2 |

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

Each issue below becomes a GitHub issue after approval, filed from the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md) as a sub-issue of #5 and replaced by a `#NNN` link in this section.
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

### 0.1 Document models, strict loader and error table [1]

Lane 0, tier T0.
Needs: nothing.

Scope: the document model with tasks keyed by id, the closed set of three task types, the `parameters` declarations and the placeholder object (codes `bad-parameter-declaration`, `placeholder-malformed` and `placeholder-in-compile`), strict fields, id rules, resource units, `allow_partial`, the simulate-only `checkpoint` flag (a boolean, default false), the strict loader with the size limit passed as `max_bytes` (code `document-too-large`, class `schema`), the error table and the schema export.
`parse_document` raises `DocumentInvalid` carrying the issues, and `Issue` is defined here, so `jobdoc` never imports `jobcheck`.
Nothing here needs the limits profile, which 0.2 adds.
The template parameters of section 0.3 shape the seed field and the arguments, and the numbers of section 0.3 apply.

Owns: `contracts/job/{README.md,VERSION,errors.yaml,errors.schema.json}`, `contracts/job/schemas/job/**`, `contracts/job/vectors/invalid/schema/**`, `packages/jobdoc/**`.

Consumes and produces: artifact contract v1 (task id pattern, reference shape), events contract v1 (identifier style); produces the document schema `schemas/job/1.0.json` and `errors.yaml` listing every code of every class with its class.

Acceptance criteria:

- [ ] `python -m wdf_jobdoc.schemagen --check` exits 0, and renaming a field of a model makes it exit 1 naming the file under `contracts/job/schemas/`.
- [ ] `pytest packages/jobdoc -q -rs` passes with no skip, and covers duplicate keys, `NaN`, a float outside `cpu`, a `cpu` with four decimals, an unknown field, a task type outside the closed set, a field not allowed for its type (for example `cpu` on a compile task), a placeholder with a second key, a placeholder as a list element or a key, and a placeholder in a compile task.
- [ ] `pytest packages/jobdoc -q -rs -k vectors` loads every file under `vectors/invalid/schema/<code>/` through `parse_document` and checks that the first issue has the code of its directory name, with `max_bytes` set to the `ci` document limit of section 0.3.
- [ ] `errors.yaml` validates against `errors.schema.json`, and the README lists the classes `schema`, `graph`, `reference`, `bundle`, `resource`, `limit`, `params` and `patch`.

Test: `packages/jobdoc/tests/test_loader.py`, `test_models.py`, `test_schema_drift.py`, `test_error_table.py` and `test_vectors.py`.

### 0.2 Registries and the limits profile [4]

Lane 0, T0.
Needs: 0.1.
Decision 8 of section 0.1 applies, and decision 9 shapes the pool table without making this issue wait for R.1.

Scope: the task type registry (a pool derived from the type, accepted kinds of in-job input references, declared outputs), the aggregation registry with its argument models and their types, the limits profile model, `limits.yaml` with the numbers of section 0.3 and the `permissive` profile, the pool table model with its three pools and `load_profile`, and a fixed copy of the slots file in the shape of `deploy/presets/slots/ci.yaml`, with a simulate entry of 250 millicores and 384 MiB, under `contracts/job/vectors/slots-fixture/`, which tests read through `slots_dir`.
The compile source bundle is `params.source` and never an entry of `inputs`, which holds only logical references between tasks of the job.

Owns: `contracts/job/{limits.yaml,limits.schema.json,registry.json}`, `contracts/job/vectors/slots-fixture/**` and `packages/jobdoc/**`.

Consumes and produces: topology v1 (pool names), the shape of `deploy/presets/slots/<preset>.yaml`; produces the registry export and the limits file.
Every test of this issue reads the slots fixture, so none depends on R.1, and the tests of the live slots files are in V.3.

Acceptance criteria:

- [ ] `pytest packages/jobdoc -q -rs` passes, including `test_registry_pools_exist_in_topology` and `test_registry_kinds_exist_in_artifact_layout`.
- [ ] `load_profile("ci", root, slots_dir=<slots-fixture>)` returns the compile, simulate and aggregate budgets of the fixture as millicores and MiB (1 CPU and 1 Gi read as 1000 and 1024, 250m as 250), shown by `test_pool_table_from_slots`.
- [ ] `test_missing_pool_is_refused` shows a slots file without a simulate entry raises `SlotsIncomplete` naming `simulate`, and the loader holds no stand-in budget.
- [ ] `test_max_job_duration_matches_compat` fails when `max_job_duration_days` differs from `wdf_compat.window.MAX_JOB_DURATION_DAYS`.
- [ ] A task type or aggregation outside the registry is rejected with `unknown-task-type` or `unknown-aggregation`, and a seed in an aggregate task with `unknown-field`, shown by `test_vectors.py` over `vectors/invalid/schema/`.

Test: `packages/jobdoc/tests/test_registry.py`, `test_limits.py`, `test_pool_table.py`.

### 0.3 Plan model, package skeletons and stubs

Lane 0, T0.
Needs: 0.2.

Scope: the plan and stats models with their schema files, the five other packages with their `pyproject.toml`, the root `pyproject.toml` and `uv.lock` edit, a stub for every cross-lane entry point, and the purity guard.

Owns: `contracts/job/check.sh`, `contracts/job/schemas/{plan,stats}/**`, the new packages until the owning lane takes over, and the root `pyproject.toml` edit.

Consumes and produces: nothing new; produces the plan and stats schemas, `check.sh` (which runs `python -m wdf_jobcheck.check`), and stubs that raise `NotImplementedError("not implemented: owned by lane V")` (or P, H, F, L) for `validate` beyond the schema and registry passes, `compile_plan`, `bind`, `patch_check`, `examples` and the CLI commands, which exit 3 with the same text.

Acceptance criteria:

- [ ] `uv sync --frozen` and `uv lock --check` exit 0 with the new members, and `python -m wdf_jobdoc.schemagen --check` exits 0.
- [ ] Every file under `contracts/job/vectors/invalid/schema/*/` exits 1 through `contracts/job/check.sh` with `reason: <code>:` for its directory name, and a valid document exits 3 with `not implemented: owned by lane V` until V.4 replaces the stub.
- [ ] `pytest packages/jobdoc -q -rs -k "no_io_imports"` passes, and fails for a fixture module that imports `os`, `sys`, `socket`, `subprocess`, `pathlib`, `tempfile`, `urllib`, `time`, `datetime`, `random`, `secrets`, `uuid`, `logging` or `wdf_events`, or calls `open` or `print`, with the three tool modules of section 3 exempt by name.
- [ ] `pytest packages/jobcheck packages/plan packages/jobpatch -q -rs -k vectors` accepts the stub error only for the names in each test's `STUBBED` list, and a real match for the rest.

Test: `packages/jobdoc/tests/test_purity.py`, `test_check_stub.py` and `packages/*/tests/test_vectors.py`.

### 0.4 Compile cache key entry input (contract change)

Lane 0, tier T0.
Needs: nothing.
Decision 10 of section 0.1 applies.
This issue merges with lane 0, so no consumer ever sees the four-input key.

Scope: add the entry module as a fifth input of `compile_cache_key` and its vectors, with the consumer list.
This is a contract-change PR on the envelope contract, so it touches the contract, the package and its tests together and bumps the contract `VERSION`.
Consumers: #6 (the workflow computes the key), #11 (the compile worker re-checks it) and this epic.

Owns: `contracts/events/{README.md,VERSION,vectors/keys.json}` and `packages/keys/**`, for this issue only.

Acceptance criteria:

- [ ] `pytest packages/keys -q -rs` passes, and `test_compile_key_entry` shows two entries over one bundle give two keys and one entry gives one.
- [ ] The formula in `contracts/events/README.md` and the vectors in `keys.json` name the entry, and `contracts/events/VERSION` is bumped.
- [ ] `pytest packages/events packages/compat -q -rs -m "not integration"` still passes.

Test: `packages/keys/tests/test_compile_key.py`, `test_vectors.py`.

### R.1 Simulate entry in the slots files (revisit of the cluster epic) [4]

Lane R, tier T0.
Needs: nothing.
Decision 9 of section 0.1 applies.

This is a `revisit` child issue of the cluster epic (#2), filed as [the method](../method.md#revisiting-a-closed-epic) says.
It links this epic (#5) and V.3 as the work that exposed the gap, lists the consumers #5, #9 and #10, names the cluster slot checks `scripts/cluster/slots.sh` and `scripts/cluster/test_slots.py` (run by `just ci`) as the verifier it touches, and blocks V.3 until it merges.
It is a contract-change style PR, so its description carries the consumer list.

Scope: a `simulate` entry in each slots file with requests equal to limits at 250 millicores and 384 MiB, under the existing `estimate: true`, the file comment updated to name the three pools, the slot test asserting the simulate slot as well, and one dated addendum line in the closure of `docs/epics/02-cluster.md`.
The figure is the largest round one that fits the simulate node of every preset after its daemons, 300 millicores and 414 MiB on `ci`, and #10 replaces it when it characterizes the pool.

Owns: `deploy/presets/slots/{ci,dev,soak}.yaml`, `scripts/cluster/test_slots.py` and `docs/epics/02-cluster.md` (the addendum line only).

Consumes and produces: the preset node sizes of `deploy/presets/{ci,dev,soak}.yaml`, read and not edited; produces the simulate entry that `load_profile` reads.

Acceptance criteria:

- [ ] `for p in ci dev soak; do scripts/cluster/slots.sh $p | grep '^slot simulate:'; done` prints three lines, each naming `250m/384Mi` and ending `fits`.
- [ ] `pytest scripts/cluster/test_slots.py -q -rs` passes, and its first test asserts `slot simulate:` in the output.
- [ ] `for p in ci dev soak; do yq '.estimate' deploy/presets/slots/$p.yaml; done` prints `true` three times.
- [ ] The consumer list and the link to the cluster epic are in the PR description, which the reviewer checks.

Test: `scripts/cluster/test_slots.py`.

### V.1 Graph validation [2]

Lane V, T0.
Needs: lane 0.

Scope: unknown dependencies, self dependencies and cycles reported with the path, over the union of `depends_on` and the edges derived from input references.

Owns: `packages/jobcheck/**` (the graph module) and `contracts/job/vectors/invalid/graph/**`.

Acceptance criteria:

- [ ] A document naming a missing task in `depends_on` exits 1 with `unknown-dependency` and a pointer to that entry, and a task listing itself with `self-dependency`.
- [ ] A three task cycle exits 1 with `dependency-cycle`, and the message lists the tasks in order and repeats the first at the end.
- [ ] `pytest packages/jobcheck -q -rs -k "property"` passes `test_cycle_path_is_a_real_cycle` (every reported path is a walk of real edges that returns to its start, on generated graphs with an injected back edge) and `test_acyclic_documents_are_accepted` (generated acyclic graphs never report a cycle).

Test: `packages/jobcheck/tests/test_graph.py`, `test_graph_properties.py`.

### V.2 Artifact reference and bundle validation [3]

Lane V, T0.
Needs: lane 0.

Scope: the reference checks of a task (producer exists, output declared by its type, kind accepted by the consumer, input counts per type, the aggregate-only form), the placeholder checks (a placeholder names a declared parameter, and the type of that parameter is the type of its position) and the resolver interface with its contract function.

Owns: `packages/jobcheck/**` (the references and resolver modules) and `contracts/job/vectors/invalid/{reference,bundle}/**`.

Consumes and produces: artifact contract v1; produces `SourceResolver`, `BundleInfo`, `BundleNotFound`, `resolver_contract(resolver)`, a `StaticResolver` test double that loads `contracts/job/vectors/resolver.json` (the CLI loads the same shape from any file), and that table, which every valid and bundle vector refers to by SHA-256.
The table holds one good bundle with a lockfile and the entry modules `echo` and `sim`, which F.1 uses and never edits, and one entry per bundle failure.

Acceptance criteria:

- [ ] Each of `unknown-producer`, `undeclared-output`, `input-kind-not-accepted`, `missing-required-input`, `too-many-inputs`, `duplicate-input`, `index-and-inputs`, `aggregate-without-input`, `index-job-not-alone`, `unknown-placeholder` and `placeholder-type-mismatch` (a `seed` placeholder in an aggregate argument, or a `string` parameter where `blocks` wants a `string-list`) has a vector that exits 1 with that code.
- [ ] A compile task that consumes a simulate output fails with `input-kind-not-accepted`, and a simulate task with no component input with `missing-required-input`.
- [ ] A bundle the resolver cannot find, one with another hash, one with no lockfile and an entry module the bundle lacks fail with `bundle-unresolved`, `bundle-hash-mismatch`, `bundle-without-lockfile` and `entry-not-in-bundle`, and an index object that does not exist with `index-unresolved`.
- [ ] `resolver_contract(StaticResolver(...))` passes, and fails for a resolver that returns a bundle with the wrong hash.

Test: `packages/jobcheck/tests/test_references.py`, `test_resolver.py`.

### V.3 Limits and resource sanity [4]

Lane V, T0.
Needs: lane 0, V.1, P.1, P.2 and R.1.

Scope: resource checks against the per-type slot budget on the document, and the size and count limits, where the plan size, critical path bound and history estimate come from `compile_plan`, `canonical_bytes` and `stats` of the plan library, so submit and placement share one number.
`pool_budget` is the one function the placement epic reads.

Owns: `packages/jobcheck/**` (the limits module) and `contracts/job/vectors/invalid/{resource,limit}/**`.

Consumes and produces: the `ci` profile and the permissive profile of 0.2; produces `pool_budget`.

Acceptance criteria:

- [ ] A simulate or aggregate task above its pool budget exits 1 with `cpu-exceeds-slot` or `memory-exceeds-slot`, and the same task passes under the `permissive` profile.
- [ ] Vectors exist for `too-many-tasks`, `max-parallel-exceeded`, `plan-too-large`, `run-timeout-too-long`, `retries-too-many`, `critical-path-too-long` and `history-budget-exceeded`, each exiting 1 with its code, and a boundary vector for each limit that exits 0 exactly at the limit.
- [ ] `pool_budget(profile, t)` equals the slot of `deploy/presets/slots/ci.yaml` for each of `compile`, `simulate` and `aggregate`, shown by `test_pool_budget_matches_slots`, and `test_live_slots_files_are_complete` loads `ci`, `dev` and `soak` through `load_profile` without `SlotsIncomplete`.

Test: `packages/jobcheck/tests/test_resources.py`, `test_limits.py`.

### V.4 The validate entry point

Lane V, T0.
Needs: V.1, V.2 and V.3.

Scope: `validate` composing the checks in a fixed order (schema, registry, graph, references, resources, limits, bundles), collecting every issue up to the cap, and `python -m wdf_jobcheck.check` behind `contracts/job/check.sh`.

Owns: `packages/jobcheck/**`.

Acceptance criteria:

- [ ] `for f in contracts/job/vectors/invalid/*/*/*.json; do want=$(basename "$(dirname "$f")"); contracts/job/check.sh "$f" 2>&1 | grep -q "^reason: $want:" || echo "MISMATCH $f"; done` prints nothing.
- [ ] A document with a cycle, an unknown output and a task above its slot budget reports all three in one call, sorted by path then code, and a document with a bad field reports only the schema pass, because the later passes need a typed document: graph, references, resources and bundles (when a resolver is given) run on it independently, and the plan checks (plan size, critical path, history) run only when graph and references found nothing.
- [ ] `test_validate_is_deterministic` shows two runs give equal reports, and `test_pass_gating` shows the order above, including that a document with a cycle and an unresolved bundle reports both.
- [ ] `STUBBED` is empty in `packages/jobcheck/tests/test_vectors.py`.
- [ ] `pytest packages/jobcheck -q -rs` passes with no skip.

Test: `packages/jobcheck/tests/test_validate.py`, `test_vectors.py`.

### P.1 Plan compiler, canonical form and manifest [5, 6]

Lane P, T0.
Needs: lane 0.

Scope: topological order with ties broken by task id, resolved defaults (compile resources, timeout and retries, default `max_parallel`), derived pools, derived edges, the `parameters` declarations carried into the plan, placeholders kept in the `params` of a template plan, the canonical bytes, `plan_hash` and `manifest_reference`.

Owns: `packages/plan/**`.

Consumes and produces: artifact contract v1 (`layout.yaml` key rule); produces the compiler and the plan identity of section 0.3.

Acceptance criteria:

- [ ] `pytest packages/plan -q -rs -k "property"` passes `test_hash_ignores_key_order_and_whitespace` (a document with its keys shuffled and its whitespace changed compiles to the same bytes and hash) and `test_order_is_topological_and_stable` (every task follows its dependencies and equal inputs give equal order).
- [ ] `manifest_reference(plan)` passes `contracts/artifact/check.sh` for kind `manifest`, and `test_reference_matches_layout` compares its key rule with `layout.yaml`.
- [ ] A document that omits compile resources compiles with the compile slot budget, 600 s and 1 retry, shown by `test_compile_defaults`, and `test_permissive_keeps_compile_defaults` shows the same document compiles to the same compile resources under `permissive`.
- [ ] `task_request_fields(task)` returns `task_type`, `cpu_cores` (millicores over 1,000) and `memory_mib` of a plan task, and `test_plan_task_maps_to_task_request` validates them, with a deadline and the input references added, against `wdf_events.models.TaskRequestData` for every task of every example, so the plan and the task request cannot drift.
- [ ] `test_template_plan_keeps_placeholders` shows a document with a `{"param": "seed"}` seed compiles to a plan that holds the same marker and the declaration, with the manifest hash unchanged by any job value.
- [ ] `test_plan_has_no_floats` fails for a plan whose canonical bytes contain a decimal point outside a string.

Test: `packages/plan/tests/test_compile.py`, `test_canonical.py`, `test_manifest.py`, `test_properties.py`.

### P.2 Plan stats and the class hint [5]

Lane P, T0.
Needs: P.1.
Decision 8 of section 0.1 applies.

Scope: the plan stats for neighbours and the class hint, with `max_width` as the widest topological level, a plain bound that `packages/plan/README.md` says is not the exact antichain, and `critical_path_timeout_bound_s` as a bound along one path that the README says is not a wall-clock bound, because a job wider than `max_parallel` also waits, so the class hint has the same limit.
`chain_count` is the number of compile tasks, and the chain of a compile task is the set of simulate tasks that name its `component` output in their `inputs`.

Owns: `packages/plan/**`.

Acceptance criteria:

- [ ] `pytest packages/plan -q -rs -k stats` passes against golden stats for a single chain, a fan-out of 8, a fan-in of 8 chains, an aggregate-only job and a document where a simulate task lists a second compile task in `depends_on` only (so `chain_count` and the chains follow data edges), including `critical_path_timeout_bound_s` as the longest path of `run_timeout_s` times one plus `retries`.
- [ ] `estimated_history_events` equals 10 plus, per task, 8 plus 6 times its retries, shown by `test_history_estimate`.
- [ ] `class_hint` returns `short` at the threshold and `long` one second above it, shown by `test_class_hint_boundary`.

Test: `packages/plan/tests/test_stats.py`, `test_class_hint.py`.

### P.3 Per-job values and binding

Lane P, T0.
Needs: P.1.
Decision 7 of section 0.1 applies.

Scope: `check_job_params` and `bind`, and the invalid job parameter vectors.
Both functions take the row's block (`seed`, `parameters`, `class_override`) as a mapping.
`check_job_params` reports `unbound-parameter` (a declared parameter with no value), `unknown-parameter` (a name the template does not declare, or `seed` inside `parameters`), `parameter-type-mismatch`, `bad-seed` and `unknown-class` itself, and `plan-too-large`.
`bind` replaces each placeholder of the template plan with the job's value by whole value, and changes nothing else.

Owns: `packages/plan/**` and `contracts/job/vectors/job-params/invalid/**`.

Consumes and produces: nothing new; produces the invalid vectors `vectors/job-params/invalid/<code>/<name>.template.json` and `<name>.params.json`, each pair self-contained, where the params file is a bare job parameter block.
The valid parameter blocks are named `<example>.<case>.json`, belong to lane F (F.1) and are bare blocks bound to the example of that name.
`check_job_params` reports `plan-too-large` when the bound plan exceeds the profile limit, so a long parameter string cannot push a stored job past the payload budget.

Acceptance criteria:

- [ ] `unbound-parameter`, `unknown-parameter`, `parameter-type-mismatch`, `bad-seed` (negative or at least 2^53) and `unknown-class` each have a vector under `vectors/job-params/invalid/<code>/<name>.params.json` next to its template, and `pytest packages/plan -q -rs -k params_vectors` shows `check_job_params` reports that code for each.
- [ ] `pytest packages/plan -q -rs -k "bind"` passes `test_bind_is_idempotent` (binding twice with the same block gives equal plans), `test_bind_replaces_only_placeholders` (every byte of the bound plan outside the replaced values equals the template plan) and `test_row_seed_binds_the_reserved_parameter`.
- [ ] The compile tasks of 1,000 jobs bound from one template are byte-identical, so `wdf_keys.compile_cache_key` gives one value, shown by `test_batch_shares_one_cache_key`, which uses the key with the entry module of decision 10.

Test: `packages/plan/tests/test_bind.py`, `test_batch_cache_key.py`.

### P.4 Versioning policy and compatibility tests [10]

Lane P, T0.
Needs: P.1 and F.2.

Scope: the additive-only rule for the document minors and the plan version, `read_plan` (version 1 only, with no hook for another version), and the evolution test.
The evolution test compares the committed schema with the regenerated one through a walker in `packages/plan` that follows `properties`, `items`, `additionalProperties`, `$ref`, `oneOf` and `anyOf`, because `wdf_compat.breaking_changes` stops at `properties` and `items` and tasks are a map of models.

Owns: `packages/plan/**`.

Consumes and produces: nothing from `wdf_compat` beyond the rule it states; produces `read_plan` and the rule in `packages/plan/README.md` that the PR introducing plan version N+1 adds the branch that reads version N, with `contracts/job/vectors/plans/N/` as its test, and keeps it for the same window as an event major.

Acceptance criteria:

- [ ] `pytest packages/plan -q -rs -k "evolution"` fails and names the field when a required field is added to a task model without a new version, and passes on the committed models.
- [ ] `test_walker_descends` shows the walker reports a new required field in a hand-built schema pair for each of the shapes `additionalProperties` with `$ref`, `oneOf` of `$ref` and `items`.
- [ ] `read_plan` reads every golden plan under `contracts/job/vectors/plans/1/`, and `test_read_plan_rejects_other_versions` shows another version raises `UnsupportedPlanVersion`.
- [ ] `test_golden_plans_still_compile` recompiles every example against the slots fixture to the committed bytes, so a change to defaults, ordering or canonical form fails and needs a contract-change PR.

Test: `packages/plan/tests/test_schema_evolution.py`, `test_evolution_walker.py`, `test_read_plan.py`, `test_golden_plans.py`.

### H.1 Patch compatibility check [7]

Lane H, T0.
Needs: P.1 and P.3.

Scope: `patch_check(old, new, completed)` over two bound plans (the CLI compiles and binds both documents), with this rule.
Resources, timeout and retries of a compile task come from the profile, and every plan task holds its resolved values, so they are compared like any other field: two plans compiled under different slots files differ and are `frozen-field-changed`.
The chain of a compile task is the set of simulate tasks that name its `component` output in their `inputs`, and `depends_on` alone never puts a task in a chain.
Whether a paused task with a committed checkpoint can resume under changed code is the checkpoint epic's contract (#18) and is not decided here: the report lists every changed task so that #18 and #24 can apply it.

| Difference between the plans | Verdict |
| --- | --- |
| a different `plan_version`, `max_parallel`, parameter declarations, task id set, type, `depends_on`, `inputs`, resource, timeout, retry budget, `checkpoint` or `allow_partial` | `frozen-field-changed` |
| any difference in a completed simulate or aggregate task | `completed-task-changed` |
| a compile task whose `source` or `entry` differs, with no completed simulate in its chain | compatible, listed in `recompile` |
| a compile task whose `source` or `entry` differs, with a completed simulate in its chain | `build-changed-after-result` |
| a not yet completed simulate whose `workload` or `args` differ (whatever its `checkpoint`), or whose `seed` differs with `checkpoint` false, or an aggregate whose `aggregation` or `args` differ | compatible, listed in `changed` |
| a not yet completed simulate with `checkpoint` true whose `seed` differs, because a checkpoint written under the old seed would be resumed under another | `seed-changed-on-checkpoint-task` |
| a completed id that is not a task of the plan | `unknown-completed-task` |
| any other difference, including a compile argument, the pool and the aggregate-only index reference | `frozen-field-changed` |

Owns: `packages/jobpatch/**` and `contracts/job/vectors/patch/**`.

Acceptance criteria:

- [ ] `contracts/job/vectors/patch/<verdict>/<case>/` holds `old.json` and `new.json` (documents), `params.json`, an optional `new-params.json`, `completed.json` and `expected.json`, where `<verdict>` is `ok` or a code, and `pytest packages/jobpatch -q -rs` compiles and binds both sides, runs every case and compares the codes, `recompile` and `changed` with `expected.json`.
- [ ] `test_every_row_has_a_case` fails unless `vectors/patch/` holds a directory for every code of class `patch` and the `ok` cases `bundle-changed`, `args-changed`, `aggregate-changed` and `checkpoint-args-changed` (a checkpoint simulate with the same seed) and `depends-only-compile-changed` (a completed simulate lists a second compile task in `depends_on` only, that compile's bundle changes, and the patch stays compatible because the simulate is not in its chain), and two `frozen-field-changed` cases, one where a patch adds a task and one where it changes a dependency.
- [ ] `test_patch_check_is_pure` shows the same inputs give the same report, and that the inputs are not mutated.

Test: `packages/jobpatch/tests/test_patch_cases.py`, `test_purity.py`.

### F.1 Example library and generator [9]

Lane F, T0.
Needs: V.4, P.1 and P.3.

Scope: the example library on the workload ladder of #11, as the documents `vectors/valid/<name>.json` named `level0-echo` (level 0 functional chain), `level1-simulate` (level 1 simulation chain with XML result blocks), `fan-out-8`, `level2-aggregate` (the chain and aggregate job), `aggregate-only` (with an index reference), `aggregate-partial` (an aggregate that opts into partial results) and `batch-template`, one bare parameter block `vectors/job-params/valid/<name>.seed-7.json` for each, and the seed list `vectors/job-params/valid/batch-template.seeds.ndjson`, plus a deterministic generator for large fan-out and fan-in jobs and for seed lists.

`level2-aggregate` and `batch-template` declare a `seed` parameter and use `{"param": "seed"}` as the seed of their simulate tasks, `batch-template` also declares an `integer` parameter that a simulate argument uses, and every other example writes literal seeds.
The seed 7 block of an example is `{"seed": 7}` plus the values that its declarations need.

Owns: `packages/jobexamples/**`, `contracts/job/vectors/valid/**` and `contracts/job/vectors/job-params/valid/**`.
Every example refers to the one good bundle of `vectors/resolver.json` and to its modules.

Acceptance criteria:

- [ ] Every file under `vectors/valid/` exits 0 through `contracts/job/check.sh`, and `examples()` lists the same names without the extension, shown by `test_examples_are_valid_vectors`.
- [ ] `contracts/job/check.sh contracts/job/vectors/valid/level0-echo.json` exits 0, and `test_level0_is_the_smoke_job` shows its task types are one compile and one simulate and no aggregate, which is the smoke job of the method.
- [ ] `generate("fan-out", 100)` and `generate("fan-in", 100)` validate under the `ci` profile with the generator's default of 0 retries, `generate("fan-out", 600)` validates under `permissive` and fails under `ci` with `too-many-tasks` among its issues, and two calls give equal bytes.
- [ ] `test_generator_rejects_over_permissive` shows `generate("fan-out", 51201)` raises `ValueError` whose message names the request and the `permissive` limit of 51,200 tasks.
- [ ] `test_seed_list_binds_to_the_batch_template` binds every line of `job-params/valid/batch-template.seeds.ndjson` to `batch-template.json` with `check_job_params` and `bind`, gets one distinct `plan_hash` per line and one `plan_hash` of the template plan, and `seed_list(5)` gives equal bytes twice.

Test: `packages/jobexamples/tests/test_examples.py`, `test_generator.py`.

### F.2 Golden plans and the fixture gate [9]

Lane F, T0.
Needs: F.1, P.3 and H.1.

Scope: the canonical manifest of each example and the bound plan of each example under its `job-params/valid/<example>.seed-7.json`, compiled against the slots fixture of 0.2, with a SHA-256 line each, and a gate that every code of `errors.yaml` has a fixture.
The gate looks for `vectors/invalid/<class>/<code>/` for the classes `schema`, `graph`, `reference`, `bundle`, `resource` and `limit`, `vectors/job-params/invalid/<code>/` for `params`, and `vectors/patch/<code>/` for `patch`.

Owns: `packages/jobexamples/**` and `contracts/job/vectors/plans/**`.

Acceptance criteria:

- [ ] `pytest packages/jobexamples -q -rs -k golden` recompiles every example against the slots fixture and compares bytes and hashes with `vectors/plans/1/`, and fails when a file and its `HASHES` line disagree or a file has no line.
- [ ] `test_every_code_has_a_fixture` fails when a code of class `schema`, `graph`, `reference`, `bundle`, `resource`, `limit`, `params` or `patch` has no file under its directory in `vectors/`, and reports the code.
- [ ] `test_plan_hash_is_deterministic` compiles and binds every example twice in separate processes and compares the hashes.
Test: `packages/jobexamples/tests/test_golden.py`, `test_fixture_gate.py`.

### L.1 Validate and plan CLI [8]

Lane L, T0.
Needs: V.4, P.1, P.2, P.3 and H.1.

Scope: the three commands with path-qualified errors, JSON output and exit codes, the profile, slots directory and resolver flags (the resolver file loaded through `StaticResolver`), and the `plan_hash` of `null` when `--params` is omitted.

Owns: `packages/jobtools/**`.

Acceptance criteria:

- [ ] `python -m wdf_jobtools validate contracts/job/vectors/valid/level2-aggregate.json` exits 0, and the same on `invalid/graph/dependency-cycle/three-task-cycle.json --json` exits 1 with an `issues` entry whose `code` is `dependency-cycle` and whose `path` is a JSON Pointer.
- [ ] `plan contracts/job/vectors/valid/level2-aggregate.json --params contracts/job/vectors/job-params/valid/level2-aggregate.seed-7.json --out DIR` writes `manifest.json` and prints `manifest_hash`, `plan_hash`, stats and the class hint, and a second run prints the same bytes.
- [ ] `plan contracts/job/vectors/valid/level2-aggregate.json --json` with no `--params` exits 0 and prints `"plan_hash": null` and a non-null `manifest_hash`, shown by `test_plan_without_params`.
- [ ] `patch-check` on the case files of `vectors/patch/ok/bundle-changed/` exits 0 and prints `recompile`, and on a `completed-task-changed` case exits 1 with that code.
- [ ] A missing file, an unknown profile and a bad flag exit 2 with a one line message.
- [ ] `validate <a bundle vector> --resolver contracts/job/vectors/resolver.json --json` exits 1 with the code of the vector's directory, and the same document without `--resolver` exits 0 with a line saying the bundle and index checks were skipped.

Test: `packages/jobtools/tests/test_cli.py`.

### L.2 Batch validation performance [11]

Lane L, T0.
Needs: P.3 and L.1.

Scope: validate one template once, then check and bind the parameters of a large number of jobs, and measure it.

Owns: `packages/jobtools/**`.

Acceptance criteria:

- [ ] `python -m wdf_jobtools.bench --jobs 100000` exits 0 and prints `template_validations: 1`, `jobs: 100000` and `seconds: <n>` with `<n>` below 60, the pass line, which is a regression guard and not a contract (a prototype of bind and hash over 100,000 jobs of 8 tasks took 2.4 s [ran]).
- [ ] `python -m wdf_jobtools.bench --tasks 512 --jobs 1000` prints the same fields for the largest template with no pass line, so that the cost of a plan at the limit is recorded, and whether the dispatch API hashes every bound plan or only the manifest is its own call (#24).
- [ ] `test_template_is_validated_once` runs 2,000 jobs and shows `validate` is called once, counted by the test and not timed.
- [ ] The `seconds` line of the first command is pasted in the PR and in Closure.

Test: `packages/jobtools/tests/test_bench.py`.

## 5. Order, stubs and spikes

- Barrier: lane 0 (0.1, then 0.2, then 0.3, with 0.4 beside them), one worker, merged alone.
  The estimate is three days.
- Beside lane 0: R.1, one worker, filed as a `revisit` child of #2 and merged before V.3 starts.
  It edits only closed-epic files, so no other lane waits for it.
- After lane 0: V.1 and P.1 start together, each in its own lane, and none needs a container.
- DAG: lane 0 (with R.1 beside it), then (V.1, V.2 ‖ P.1, then P.2 and P.3), then V.3 after P.2 and R.1, then (V.4 ‖ H.1 after P.3), then (F.1 after V.4, P.1 and P.3, then F.2 after F.1, P.3 and H.1 ‖ L.1 after V.4, P.2, P.3 and H.1), then (P.4 after F.2 ‖ L.2 after L.1), then the exit test.
- All four questions of the first draft are answered (decisions 7 to 10), so no default is pending, and a later change to any of them is a contract-change PR.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| `validate`, `compile_plan`, `bind`, `patch_check`, `examples` and the CLI commands in 0.3 | `NotImplementedError("not implemented: owned by lane V")` and the same for lanes P, H, F and L | each package's `test_vectors.py` accepts that error only for the names in its `STUBBED` list and a real match for the rest, and the owning issue empties the list |
| Simulate entry of the slots fixture | the simulate entry that R.1 adds to the live slots files, until it merges | the pool table tests of 0.2 read the fixture, and `test_live_slots_files_are_complete` of V.3 loads the live files through the same loader |
| `StaticResolver` | the resolver the dispatch epic (#24) builds on the artifact client | `resolver_contract(resolver)` runs against the double and, in #24, the real one, in one test of that epic |

Spikes: none.
The history event constants and the batch timing are estimates and a guard, recorded as such, and no lane waits on a measurement.

## 6. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main`, on this host with no cluster started, and with Docker running only for row 14.
Each command runs from the export root as written, its real output is pasted in the PR that records the run, and it is summarised in Closure.
The steps that change a file do so in the scratch export and restore it afterwards.

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

- Maximum parallel workers: 2 for lane 0 beside R.1, then 2 (V.1 and P.1), then 3 at the widest point (V, P and H), then 2 (F.2 beside L.1, then P.4 beside L.1 or L.2), then 1 for the exit test.
- Host constraints: every issue is T0 and needs only `uv` and Python from the dev shell, so there is no limit on a worker beyond CPU.
  Every lane's Done gate runs `just ci`, which runs `just itest`, so each gate needs Docker for the envelope integration tests that already exist on `main`.
  The new packages start no container.
- Estimates, not measurements: lane 0 (3 d), lane V (3.5 d), lane P (3.5 d, P.4 waits for F.2), lane H (1.5 d), lane F (2 d), lane L (2 d), lane R (0.5 d, beside lane 0), exit test (1 d).
  That is about 10 days elapsed along lane 0, V.1 to V.4, F.1, F.2, P.4 and the exit test, and about 16.5 worker-days of effort, with `uv.lock` rebases and the cross-lane Needs as the main sources of slip.

## 8. Closure (filled at close)

<commit sha, exit test run, CI run url, what was learned, what moved>

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).
