# Epic #11: Workload toolchain, guest contract and aggregate task - breakdown

Status: approved
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/11
Architecture note: docs/architecture/14-wasm-workload-library.drawio
Verifier: none, per decision 16.
Depends on (closed epics): Dev environment (#1), Fixed-resource cluster (#2), Job event envelope (#4), DAG configuration schema and validator (#5), Durable state and artifact storage (#3), Artifact passing (#12)

Evidence marks: `[read]` means read on `main` at `307a54a` on 2026-11-08 while preparing this spec, and nothing was executed beyond read-only commands.
The epic issue body was read on 2026-11-08.
The S6 result (`docs/delivery/spikes/S6/cold-start.md`) landed after this spec and was read on `main` at `8ef24ae` on 2026-11-09, and the marks `[read at 8ef24ae]` refer to it.
Method: [docs/delivery/method.md](../method.md).
Model: [docs/delivery/epics/06-artifact-passing.md](06-artifact-passing.md), whose structure this spec follows.
Tiers: T0 is host Python with no runtime, T1 adds real tools or containers (here the real `componentize-py` and `wasmtime` on the host, and SeaweedFS through `wdf_itest` for the aggregate tests that read a store), and T2 is the cluster from `just up` on preset `ci`.
This epic has no T2 work: nothing in it runs inside a pod until the workers epic (#10) exists.
A test that needs a container carries the `integration` marker and so runs in `just itest` and in every lane's `just ci`.
Tests that build a component or run `wasmtime` need no container and run in `just test`, with the built component cached per pytest session.

## 0. Decisions for the captain

Section 0.1 lists what is already decided and carried, 0.2 the questions, all answered, 0.3 the defaults that the lanes build on, and 0.4 the proposals that lane 0 and the issues pin.
A change to a default or a proposal changes only the issues named in its last column.

### 0.1 Recorded decisions

Everything in the epic body stands except where merged work has overtaken it, which this table and section 1 list.

| # | Decision | Source | Applied in |
| --- | --- | --- | --- |
| 1 | Python models are the source of anything with a schema, and the exported JSON Schemas are checked in with a drift test | envelope epic decision 5, carried by every later epic | 0.1 (issue), 0.4 |
| 2 | `just` stays the entrypoint with logic in scripts. This epic adds no recipe and no `just/*.just` module: tests run through `just test`, `just itest` and `just ci`, and each library has a `python -m` command | dev environment epic, carried by the artifacts epic decision 3 | sections 3, 4 |
| 3 | Runtime libraries read their contract files through package data and never by repository path, and every package has a wheel test | state epic decision 11, carried | 0.1, 0.2, every package issue |
| 4 | General docs do not hard-code counts | artifacts epic decision 6 | Closure |
| 5 | The compile cache key is `wdf_keys.compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version, entry)`, five inputs, computed nowhere else. Two entries over one bundle are two builds. This replaces the four-input key of the epic body | `contracts/events/README.md:81-89` (events v3) [read] | C.1, R.1 (pins) |
| 6 | The compile task returns the references of `component.wasm` and `build-info.json` and writes no database row. The workflow looks up the cache before placing a compile task, links a hit with `cache.use`, and records the entry with `cache.record` after the commit. The build library therefore has no cache step: the "cache check" and "write the cache entry" steps of the epic body move to the workflow (#6). This confirms the amendment that the artifacts spec asked this epic to confirm | `docs/delivery/epics/06-artifact-passing.md` decisions 18 and 19, rules table row for #11 [read] | C.1 |
| 7 | The staging contract fixes the output file name of each task type and the input file the worker writes, and an undeclared file is skipped and listed. This spec confirms the names, as the artifacts spec asked | `contracts/artifact/staging.yaml` (artifact v3) [read] | 0.1, W.1, A.1 |
| 8 | The aggregation registry is closed and holds `summary_stats` (optional `blocks` and `columns`, string lists) and `digest_list` (no arguments). This spec confirms the argument names, as the validator spec asked | `contracts/job/registry.json` (job v2), `docs/delivery/epics/04-validator.md:62` [read] | A.1, K.3 |
| 9 | The aggregate reads its inputs through `wdf_artifactclient.aggregate.read_index` and `open_results`, in index order, which is ascending task id. This replaces "sorted (job, seed) order" of the epic body, and the output is deterministic for the same reason | artifact v3, `aggregate.py:100-170` [read] | A.1 |
| 10 | The compile step installs from the wheelhouse mirror with `--no-index --find-links --require-hashes`, and the aggregate pool gets no package index. The wheels the mirror serves beyond its fixture are this epic's to supply, and none is needed by this epic's bundles | `deploy/mirror/README.md:7,12`, `docs/delivery/epics/02-cluster.md:69` [read] | C.1, C.2 |
| 11 | A compile task names `params.source` and `params.entry` and has no resources, timeout or retries of its own: the compile slot applies, with `compile_run_timeout_s` and `compile_retries` of the limits profile. A simulate task names `workload`, `seed` and `args` | `contracts/job/` job v2 [read] | 0.1, C.1, W.1 |
| 12 | The bundle format is this epic's, and the dispatch epic (#24) builds the real `SourceResolver` on the artifact client, so this epic ships the function that turns a stored bundle file into a `BundleInfo` (`bundle.info(path)`) | `docs/delivery/epics/04-validator.md:111-112` [read] | C.1 |
| 13 | `paced_sim`, the kill-and-resume test and the guest side of the checkpoint format (detail issues 5 and 12) move to the checkpoint epic's pass (#18), with `/ckpt` and `/resume` as #18 defines them | `docs/delivery/method.md:274` [read] | section 1 |
| 14 | No new CI workflow. The `ci` workflow runs `just ci`, which runs every test of this epic | review of the method and the earlier specs | section 7 |
| 15 | The `wasmtime 48.0.7` and `wasm-tools 1.262.0` pins are delivered by hash-checked fetch and are not rebuilt here | `nix/wasm-tools.nix`, `docs/versions-policy.md` [read] | section 1 |
| 16 | No verifier for this epic: no `verify-workloads` skill, every issue names its tests, and the exit test is a command checklist (question Q1, answer (a)) | captain | sections 3, 6, every issue's Verifier |
| 17 | Level 0 workloads write `results.xml` in `wdf.sim.v1`, with small block kinds (`echo`, `scalar`, `digest`), and the staging contract does not change (question Q2, answer (a)) | captain | 0.1, W.1, K.2 |
| 18 | The workers epic (#10) builds every pool image, this epic pins the contents as installable packages plus the `wasmtime` and `wasm-tools` release assets of `nix/wasm-tools.nix`, and ships no Dockerfile (question Q3, answer (a)) | captain | 0.2, A.2, section 7 |
| 19 | The "fail once per attempt" fault mode is dropped from version 1, and #15 shows a retry that succeeds with an infrastructure fault (a killed worker) instead (question Q4, answer (a)) | captain | 0.1, W.3 |
| 20 | No submit-time resource check in version 1: the runtime library refuses a task whose guest cap is below the component minimum as a deterministic `guest_memory_cap` before starting `wasmtime`, and the number is published in `wdf_runtime.pins` as `GUEST_MIN_PAGES` (question Q5, answer (a)). The minimum is the initial memory the workload component declares: 0.2 creates the constant with the 206 pages of S6's stand-in guest, and R.2 sets it from the real component with a test that fails when the built component declares more. A component that declares more than the constant fails at instantiation instead, and R.2 pins that stderr string so `classify` gives the same class | captain, `docs/delivery/spikes/S6/cold-start.md` [read at 8ef24ae] | 0.2, R.1, R.2 |
| 21 | S6 passed, and a first run keeps its compile memory: in the `ci` simulate slot, after a cold compile a guest that held 64 MiB more completed and one that held 128 MiB more was OOM killed with exit 137. The first run of a component on a pod is therefore a separate warm-up process that runs `noop` and fills the module cache, with the command line from `wdf_runtime.warm_up`, and the task's guest then runs warm in a new process. That makes `GUEST_OVERHEAD_MIB` the warm figure, 128 MiB. The warm-up has no `-W timeout`, and R.2 pins whether `-W timeout` counts compile time | `docs/delivery/spikes/S6/cold-start.md` [read at 8ef24ae] | 0.4, R.1, R.2, rules row #10 |

### 0.2 Questions for the captain

All questions are answered: the captain took the recommended option of Q1 to Q5, recorded as decisions 16 to 20 in section 0.1.

### 0.3 Defaults the lanes build on

These carry the remaining open questions of the epic and the choices found while reconciling it.

| # | Topic | Options | Recommendation | Default if no answer, and the consequence of the alternative | Changes |
| --- | --- | --- | --- | --- | --- |
| D1 | A second source language | (a) none in version 1, the Rust `wasm32-wasip2` spike (detail issue 13) stays unfiled, (b) file it now | (a), nothing needs native speed yet | (a). With (b) one more toolchain to pin | none |
| D2 | Calibration tolerance and recalibration | (a) 20 percent, recalibrate on every bump of `wasmtime` or `componentize-py` and on a new host, (b) a tighter band | (a), the epic's proposal | (a). With (b) the catalogue check fails on noisy CI runners | R.3 |
| D3 | Cooperative stop marker file | (a) deferred to #18 with the checkpoint pass, (b) now | (a), it only matters for checkpoint-capable work | (a) | none |
| D4 | Aggregate input scale | (a) one task over one index with a per-file reduce, a tree of aggregates only after #16 measures store latency at 100,000 results, (b) a tree now | (a), the epic's proposal, and memory follows the largest single result | (a). With (b) the workflow needs a fan-in shape that the job schema does not have | A.1 |
| D5 | Float determinism of `summary.json` | (a) the bytes are deterministic for pinned `pandas` and `numpy` (index order, per-file partials combined in that order, canonical JSON with `repr` floats), and the host check compares `count`, `min` and `max` exactly and `mean` and `std` within a relative 1e-9, (b) exact equality with an independent host computation | (a). An independent host computation in pure Python cannot match a `numpy` reduction bit for bit | (a). With (b) the host check would have to reuse the aggregate's own code, which proves nothing | A.1, K.3 |
| D6 | Evolution of `wdf.sim.v1` | (a) additive only inside `wdf.sim.v1` (new block kinds, new columns), several blocks in one file, (b) one file per block | (a). The staging contract already fixes one `results.xml` per attempt | (a). With (b) a staging contract change | 0.1 |
| D7 | Precompiled `.cwasm` | (a) not in version 1, simulate pods keep `wasmtime`'s module cache on scratch (#10), (b) a compile output now | (a). S6 passed, so the cold start needs no `.cwasm` under the warm-up rule of decision 21 | (a). With (b) a new artifact kind, a layout change and a contract change with #12 | R.2 |
| D8 | Compile image digest in the cache key | (a) no: the five inputs stay, because the lockfile pins every installed wheel by hash and the guest Python and `componentize-py` are in the key through its version, (b) yes | (a). The key formula is pinned in events v3 | (a). With (b) a contract-change PR on the events contract (consumers #6, #12) and every cache entry is rebuilt | none |
| D9 | No network for the `componentize-py` step | (a) the build library runs that step under `unshare --user --net` when the kernel and the container profile allow it, and S1b records whether they do in a container with the default seccomp profile; author import-time code already runs inside the wasm sandbox, (b) a separate container for the step (#10) | (a) | (a). With (b) the compile runner of #10 runs two containers per task | C.2, S1b |
| D10 | Deterministic compile failures cached | (a) no in version 1, (b) yes | (a), the epic's proposal | (a) | none |
| D11 | A `DOCTYPE` in a result | (a) the aggregate and the host check refuse any document with a `DOCTYPE` as `aggregate_parse_failed`, (b) rely on the parser's amplification limit only | (a), the epic proposed it and did not test it, so A.2 tests it | (a) | A.2, K.2 |
| D12 | Result rows that the host recomputes | (a) the kernel generates every row of every level 1 block from (seed, args) and the host check compares parsed values, with a `--max-rows` guard and small-work variants for large runs, (b) a digest only | (a), the epic's key decision, with the guard from its risk "the host-side digest check costs host CPU" | (a) | K.1, K.2 |

### 0.4 Proposals the lanes pin

After lane 0 merges, `contracts/workload/README.md` and its files are the source and this section is only a pointer.

| Item | Proposal | Epic item |
| --- | --- | --- |
| Contract directory | One contract, `contracts/workload/` (owner #11), with `VERSION`, `README.md`, `check.sh`, the WIT world under `wit/`, and the files below, each with a generated schema where it has one and vectors under `vectors/<name>/{valid,invalid/<reason>}/`. Loader `python -m wdf_runtime.contract --check FILE` (exit 0 valid, 1 with `reason: <code>: <detail>` on stderr, 2 usage), generator `python -m wdf_runtime.gen [--check]`. The models live in `wdf_runtime`, because the kernel must stay standard library only to run inside a guest | contract 7 of the method |
| WIT world | `contracts/workload/wit/task.wit`: `package wdf:task@1.0.0; world task { include wasi:cli/command@0.2.9; }`, with the WASI 0.2.9 WIT packages vendored under `wit/deps/`. The WIT contract version is the version in the `package` line, separate from the contract `VERSION`, so a change to the exit table or the result schema never changes a cache key. The guest contract is versioned by the WIT version, as the epic says | detail issue 14 |
| Input file | `/in/input.json` is the canonical JSON (sorted keys, no whitespace, a final line feed) of the bound simulate `params`, exactly `{"args": {...}, "seed": n, "workload": "..."}`. The worker (#10) takes the params from the activity payload that #6 pins, because the task request carries none (`TaskRequestData` [read]). `/in` is a fresh directory holding only a copy of `input.json`, mounted with `--dir HOST::/in`, and the runner re-hashes it after the run, because Wasmtime 48 has no read-only mount. Schema `input.schema.json` | guest contract |
| Error file | `/out/error.json`: `{"code": "<slug>", "message": "<text>", "deterministic": true\|false}`, at most 4 KiB. The staging library skips it as an undeclared file, and the runner reads it locally and puts `<class>: <code>: <message>` in the failure reason. Schema `error.schema.json` | guest contract |
| Heartbeat | One stderr line `HEARTBEAT {"progress":f,"step":n,"steps":N}` (compact JSON, `progress` from 0 to 1), pinned by a regular expression and vectors. Cadence is the `heartbeat_every` argument of each level 1 workload. The aggregate library emits the same line once per input | guest contract |
| Exit class table | `contracts/workload/exit-classes.yaml`: per pool, each class with its trigger, whether it is deterministic (`true`, `false` or `from-error-file`) and the exit code of the native libraries. Simulate: `ok`, `timeout`, `guest_memory_cap`, `guest_failed`, `host_error`, `oom_killed`, where `guest_failed` also covers a `results.xml` above its cap in the staging contract after a zero exit, deterministic, with the reason `guest_failed: output-over-cap: <bytes>`. Compile: `compile_failed`, `compile_dependency_unavailable`, `compile_oom`, `compile_timeout`. Aggregate: `aggregate_input_unavailable` (`STORE_UNAVAILABLE`), `aggregate_input_corrupt` (`ARTIFACT_LOST` with its cause), `aggregate_input_refused` (an index the reader refuses: foreign tenant, partial refused, proposed here because the epic list has no class for it), `aggregate_parse_failed`, `aggregate_oom`, `aggregate_timeout`, `aggregate_failed`. Native exit codes, per class: 0 `ok`, 1 an unexpected error of the library (a bug, which is `aggregate_failed` for the aggregate), 2 usage, then compile 3 `compile_failed`, 4 `compile_dependency_unavailable`, 5 `compile_timeout`, and aggregate 3 `aggregate_input_unavailable`, 4 `aggregate_input_corrupt`, 5 `aggregate_input_refused`, 6 `aggregate_parse_failed`. `compile_oom`, `aggregate_oom` and `aggregate_timeout` have no native code: the runner decides the two OOM classes from exit 137 with node events and `aggregate_timeout` from its own deadline, since the aggregate command takes no `--timeout` (#10). The simulate classes come from `classify`, and `python -m wdf_runtime run` has its own codes (Runtime command line). The last stderr line of a native library is `reason: <class>: <detail>`. #6 maps the classes into its failure class table, as it maps the artifact codes | detail issues 2, 9 |
| Result format | `contracts/workload/results.yaml` (schema `results.schema.json`) is the schema file of `wdf.sim.v1`: root `results` with `schema="wdf.sim.v1"` and `seed`, `block` elements with `id` (unique in the file) and `kind`, `row` elements whose values are attributes, UTF-8, no namespaces, no `DOCTYPE`, no processing instruction beyond the XML declaration. Each kind lists its columns and their type: `int` (decimal, no sign for zero, no leading zeros), `float` (Python `repr`, finite only), `hex` (lower case), `str` (attribute escaping). Kinds proposed: `echo` (`key` str, `value` str holding canonical JSON), `scalar` (`name` str, `value` int), `digest` (`name` str, `value` hex), `series` (`step` int, `value` float), `payload` (`step` int, `data` hex). Valid and invalid vectors include the malformed block fixture that #15 and #17 use | detail issue 16 |
| Summary format | `summary.json`, canonical JSON, schema `summary.schema.json`: `schema` (`wdf.summary/1`), `aggregation`, `args`, `inputs`, `missing` (task ids, sorted), `rows`, and for `summary_stats` `blocks.<block id>.<column>` with `count`, `mean`, `std` (sample, `null` when `count` is 1), `min`, `max` over numeric columns, and for `digest_list` `digests` as `[{"task", "seed", "digest"}]` in index order | detail issue 7 |
| Build info | `build-info.json`, schema `build-info.schema.json`: `schema` (`wdf.build-info/1`), `cache_key`, `inputs` (the five key inputs), `tools` (`componentize_py`, `wasm_tools`, `uv`, `guest_python`), `component` (`sha256`, `size`), `imports` and `exports` as `wasm-tools` reports them, `timings_s` (`unpack`, `install`, `componentize`, `check`). It is stored by its own hash (artifacts decision 18) | detail issue 8 |
| Bundle format | A POSIX tar archive, uncompressed, at most the `source` cap of the staging contract (64 MiB). The root holds `pyproject.toml`, `uv.lock` and the source modules. `modules` of the `BundleInfo` are the dotted names of every `.py` file and package under the root. Unpacking uses `tarfile` with `filter="data"`, refuses links and devices, at most 10,000 entries and 256 MiB unpacked (estimates). `lockfile_sha256` is the SHA-256 of the bytes of `uv.lock`. A bundle builder writes entries sorted with zero times and owners, so the same tree gives the same bytes | detail issue 8, validator decision 12 |
| Build procedure | `python -m wdf_compile build --bundle B.tar --entry MODULE --out DIR [--find-links URL] [--timeout S]`: unpack; install the locked dependencies with `uv export --frozen --no-dev --no-emit-project` into `uv pip install --target <site> --no-index --find-links URL --require-hashes --no-build --python-version 3.14`; run `componentize-py` against the WIT tree that `wdf_runtime` ships as package data (a copy of `contracts/workload/wit`, decision 3) and world `task`; check with `wasm-tools component targets`; write `component.wasm` and `build-info.json` into `DIR`. Exit codes from the exit class table, and `cache_key: <hex>` on stdout. No cache I/O, no store I/O: the worker stages and collects (decision 6) | detail issue 8 |
| Pins | `wdf_runtime.pins` holds `COMPONENTIZE_PY_VERSION` (`0.25.1`), `WIT_VERSION` (read from `task.wit`), `WASMTIME_VERSION` (`48.0.7`), `GUEST_OVERHEAD_MIB` (128, S6's warm figure, which holds only under the warm-up rule of decision 21) and `GUEST_MIN_PAGES` (decision 20), so the workflow (#6) computes the key without installing `componentize-py`. A test holds each constant equal to `uv.lock`, the installed package metadata and `nix/wasm-tools.nix`, so the copy cannot drift | detail issue 1 |
| Runtime command line | `wdf_runtime.command(component, scratch, memory_mib, timeout_s, cache_dir=None) -> argv`: `wasmtime run` with `-W max-memory-size=` (memory_mib minus `GUEST_OVERHEAD_MIB`), `-W timeout=` (omitted for paced work, which counts sleep), `--dir` for `/in` and `/out`, the module cache option when `cache_dir` is given, no `--env`, no stdin. `wdf_runtime.classify(exit_code, stderr_tail, error_file, runner_facts) -> Classified(cls, deterministic, reason)`. `wdf_runtime.warm_up(component, scratch, cache_dir) -> argv` is the warm-up of decision 21: the same `wasmtime run` over a `noop` input that the library writes, with the module cache option and no `-W timeout`, run once per component per pod before its first task. `python -m wdf_runtime run --component C --input input.json --out DIR [--memory-mib N] [--timeout-s S] [--cache-dir D]` prints `class: <class>`, `heartbeats: <n>` and exits 0 for `ok`, 1 for any other class, 2 usage, and with `--cache-dir` it runs the warm-up first when the cache has no entry for the component | detail issue 9 |
| Workload library | One entry module `wdf_workloads.main` with the `Run` export class that the command world requires, dispatching on `params.workload`, and every import at the top of a module. Level 0: `echo` (returns its args and seed), `sum_squares` (`n`), `hash_chain` (`rounds` of SHA-256 from the seed as 8 bytes big endian). Level 1: `noop`, `cpu_burn` (`units`), `mem_hold` (`mib`, `units`), `io_churn` (`rows`, `row_bytes`), each with `heartbeat_every`. `fault` with `mode` of `exit_nonzero`, `trap`, `memory_cap`, `memory_kill`, `hang`, `slow_start`, `fail_late` (`at_progress`). An unknown workload writes `error.json` with code `unknown-workload` and exits nonzero. `python -m wdf_workloads.bundle --out DIR` writes `bundle.tar` with `wdf_kernel`, `wdf_workloads`, a `pyproject.toml` and a `uv.lock` with no dependencies | detail issues 4, 6 |
| Kernel | `wdf_kernel`, standard library only, runs on host Python 3.13 and guest CPython 3.14: SplitMix64, FNV-1a 64, the row generator of each workload, `expected_blocks(workload, seed, args)`, `expected_digest(workload, seed, args)` over parsed values, the streaming writer, the format rules of `wdf.sim.v1` in `wdf_kernel.format`, which the contract loader calls for `.xml` files so the refusal reasons have one implementation, the streaming reader (`ET.iterparse`, a flatten that carries the block id, `DOCTYPE` refusal, and a `max_bytes` size cap that the aggregate sets to the `results.xml` cap of the staging contract), and `expected_summary(aggregation, args, inputs)` in pure Python, where `inputs` maps each task id to its input params, or to `None` for a missing input, and is read in ascending task id. `python -m wdf_kernel.check results --input input.json --results results.xml [--max-rows N]` and `python -m wdf_kernel.check summary --summary summary.json --inputs TASK=A.json TASK=B.json... [--missing TASK...]` exit 0 equal, 1 not, 2 usage, and print `digest: <hex>` | detail issues 3, 18 |
| Aggregate | `wdf_aggregate`: `run(client, index_reference, tenant, aggregation, args, out_dir)` reads through `read_index` and `open_results`, flattens each file with `iterparse` into a frame, reduces per file, combines the partials in index order, records the missing inputs when the index allows partial results, and writes `summary.json`. `python -m wdf_aggregate run --index REF.json --tenant T --aggregation NAME --args JSON --out DIR --endpoint URL --credentials FILE --role aggregate` and `python -m wdf_aggregate files --aggregation NAME --args JSON --out DIR [--missing TASK...] TASK=RESULTS...` (local files, for the host check and the exit test), which names each file's task id and combines the files in ascending task id, the order of an index, whatever the order of the arguments. Exit codes and the last `reason:` line from the exit class table. It prints `peak_rss_mib` from `resource.getrusage` for the record | detail issue 7 |
| Calibration | A work unit is a fixed loop of the kernel. `python -m wdf_runtime.calibrate --component C [--runs 3] [--check CATALOGUE]` runs `cpu_burn` in the guest, prints `units_per_second`, and with `--check` exits 1 when the measurement differs from the catalogue by more than D2's tolerance. The catalogue `wdf_runtime/catalogue.yaml` records the host, the versions, the CPU basis (`host`: one uncapped core), the rate and the nominal duration of each level 1 argument set of K.3. A later epic derives the duration of its own argument sets from the rate, and a duration in a pod is the host duration divided by the pod's CPU in cores, four times longer in a 250m simulate slot, since a guest runs on one thread | detail issue 10 |
| Heavy-compile bundles | `python -m wdf_compile.heavy --size-mib N --out DIR` writes one bundle with one generated module of about N MiB of source. S1b records the smallest N whose build exceeds the `ci` compile slot, and C.2 uses that N and a smaller N whose build exceeds a short `--timeout`. No dependency set is needed, so the mirror fixture is not extended | detail issue 15 |

### Rules agreed with epics that do not exist yet

These are stated so the owner epic confirms them in its own spec.

| Epic | Rule |
| --- | --- |
| #6 Workflow | Computes the cache key with `wdf_keys.compile_cache_key` from the `BundleInfo` of the dispatch resolver, `params.entry` and `wdf_runtime.pins`, looks it up before placing a compile task and records it after the commit (decision 6), and refuses to record an entry whose `build-info.json` carries another `cache_key` than the one it computed. Passes the bound `params` of each task to the worker in the activity payload it pins. Maps the classes of `exit-classes.yaml` into its failure class table and does not retry a deterministic class. Aggregate waits for every input unless the task sets `allow_partial` |
| #10 Workers | Builds the pool images per decision 18. Runs `wasmtime` with `wdf_runtime.command` and classifies with `wdf_runtime.classify`, mounts a copy of `input.json` at `/in` and re-hashes it after the run, mounts the staging `output/` at `/out`, parses `HEARTBEAT` lines into heartbeats, and decides `oom_killed`, `compile_oom` and `aggregate_oom` from exit 137 with node events. Before the first task of a component on a pod, when the module cache has no entry for it, runs the warm-up of `wdf_runtime.warm_up` as a separate process (decision 21), with a heartbeat timeout above the 30 s a first run takes at 250m. Classifies a simulate `results.xml` above its staging cap as `guest_failed` before `collect_outputs` refuses it. Runs `python -m wdf_compile build` on the compile pool with the mirror as `--find-links`, and `python -m wdf_aggregate run` on the aggregate pool. Keeps the module cache on scratch |
| #18 Checkpoint | Owns the checkpoint format, `paced_sim`, the cooperative stop marker and the kill-and-resume test, and builds them on this epic's kernel, workload library and runtime library |
| #24 Dispatch | Builds the real `SourceResolver` on `wdf_compile.bundle.info(path) -> BundleInfo` after the bundle is stored |
| #15 Failure scenarios | Calls the `fault` modes, the malformed block vectors and the heavy-compile bundles, and owns the scenarios |
| #17 Acceptance | Uses the level 0 vectors of `contracts/workload/vectors/level0/`, the level 1 argument sets of K.3, `wdf_kernel.check` and `expected_summary` |
| #13 Observability | Exit classes come from the runner; the aggregate's input and row counts are fields of `summary.json`, and its duration is the runner's |
| #14, #16 | Read the calibration catalogue and `expected_digest`, derive the duration of their own argument sets from the catalogue rate, and divide by the pod's CPU in cores, since the catalogue's basis is one uncapped host core |

## 1. Goal and non-goals

Goal: compilation is a platform task and a guest is a plain command component.
The compile task builds an author's Python source and locked dependencies into a WebAssembly component with `componentize-py`, the simulate task runs it with stock `wasmtime`, and the aggregate task is native Python over the XML result blocks the simulations wrote.
Every simulate and aggregate output is a deterministic function of (seed, params), so any result is verified on the host without running WASM.
A compile output is identified by its cache key and need not be byte-stable, and two builds of one bundle must behave the same (exit row 8).

### Delivered already, and what is left

| Epic body item | State on `main` | Where it is, or what is left |
| --- | --- | --- |
| `wasmtime 48.0.7` and `wasm-tools` pins | Delivered | `nix/wasm-tools.nix`, `docs/versions-policy.md` |
| `componentize-py 0.25.1` pin | Left | `uv.lock`, issue 0.2 |
| Cache key function | Delivered with five inputs | `wdf_keys.compile_cache_key` (decision 5) |
| Compile result cache: entry, lookup, use, record, expiry | Delivered by #3 and #12 | `wdf_artifactclient.cache`, `wdf_artifactindex` (decision 6) |
| Staging, file names, caps, `OUTPUT_MISSING`, output tail | Delivered by #12 | `contracts/artifact/staging.yaml` (decision 7) |
| Aggregate index format, reader, `open_results` | Delivered by #12 | `wdf_artifactclient.aggregate` (decision 9) |
| Aggregation registry | Delivered by #5 | `contracts/job/registry.json` (decision 8) |
| Package source for the compile step | Delivered by #2 | `deploy/mirror/` (decision 10) |
| Platform WIT contract and conformance check | Left | 0.1, C.1 |
| Guest contract, exit class table, result and summary formats, build info, bundle format | Left | 0.1 |
| Deterministic kernel, streaming writer and reader, host checks | Left | K.1, K.2, K.3 |
| Workload ladder levels 0 and 1, fault workload | Left | W.1, W.2, W.3 |
| Compile task library, limits, heavy-compile bundles | Left | C.1, C.2, S1b |
| Runtime library, Wasmtime pin tests | Left, S6 delivered | R.1, R.2, and `docs/delivery/spikes/S6/cold-start.md` (decision 21) |
| Calibration | Left | R.3 |
| Aggregate library and its hardening | Left | A.1, A.2 |
| Aggregate image and the compile image | Per decision 18 | #10 |
| `paced_sim`, kill-and-resume, guest checkpoint format | Deferred to #18 | decision 13 |
| Rust spike | Not filed | D1 |

Non-goals for this pass:

- everything the epic lists under "Does not own": the worker process, process groups, heartbeats to Temporal, pod-level OOM detection and slot sizing (#10), checkpoint storage and the pause and resume flow (#18, #6), artifact storage and the cache semantics (#12, #3), the job schema and registry (#5), scenario mixes (#14, #16), failure injection scenarios (#15), native extension wheels for guests, WASI 0.3, guest networking, hardening beyond Wasmtime defaults
- every run inside a pod: compile on the compile pool, simulate on the simulate pool and aggregate on the aggregate pool need the workers epic (#10), so this epic touches no cluster
- the workflow's use of the cache and of the exit classes (#6)
- the pool images, per decision 18
- the items of decision 13 (#18)

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with `uv`, Python 3.13, `wasmtime 48.0.7`, `wasm-tools 1.262.0`, `pytest`, `hypothesis`; recipes `just test`, `just itest`, `just ci` | `flake.lock`, `nix/wasm-tools.nix`, `uv.lock` | none, exists [read] |
| Cluster | `ci` slot sizes, the mirror module and its install flags | `deploy/presets/slots/ci.yaml`, `deploy/mirror/` (module v1) | none, exists. S1b measures against these numbers, and S6 measured the simulate slot |
| Envelope | `wdf_keys.compile_cache_key` (five inputs), the artifact reference and layout | events v3, artifact v3, keys v1 | none, exists |
| Validator | Compile, simulate and aggregate params, the registry, `compile_run_timeout_s`, `BundleInfo` and the `SourceResolver` protocol | job v2, `wdf_jobcheck` validator v1 | none, exists |
| Artifacts | Staging contract and `wdf_artifactclient.staging`, `aggregate.read_index` and `open_results`, `ArtifactClient`, the typed errors, the store fixture `store_session` | artifact v3, artifactclient v1, store v3 | none, exists |
| Workflow (#6) | The activity payload that carries the bound params to the worker | not pinned yet (contract 4, E8) | tests call the libraries with params directly |
| Workers (#10) | The runner | not built | `wdf_artifactclient.testing` runs a stand-in guest that is a Python callable, so a `wasmtime` runner cannot join its `RUNNERS`. R.1 adds its own test that runs a real component in a `make_scratch` directory and checks `output/` against the staging contract, and `RUNNERS` is unchanged |

No `revisit` child issue is open against the dev environment, cluster, envelope, validator, state or artifacts epics that blocks this epic, and this spec adds none under its defaults.
The names below were checked free on `main` [read: no `packages/{kernel,workloads,compile,runtime,aggregate}/`, no `contracts/workload/`, no `docs/delivery/spikes/S1b/`], and `docs/delivery/spikes/S6/` holds the S6 result [read at 8ef24ae].

### Produces (each pinned precisely)

| To | What | Where it lives (path) | Shape | Version |
| --- | --- | --- | --- | --- |
| #6, #10, #13, #15, #17, #18, #24 | Workload contract: WIT world, guest contract (input, error file, heartbeat), exit class table, result format, summary format, build info, bundle format, level 0 golden vectors | `contracts/workload/` | `wit/task.wit` and `wit/deps/`, `exit-classes.yaml`, `results.yaml`, generated `input.schema.json`, `error.schema.json`, `summary.schema.json`, `build-info.schema.json`, `results.schema.json`, `exit-classes.schema.json`, `vectors/`, `check.sh`. Loader and generator as 0.4. Reasons: `schema-violation`, `duplicate-class`, `unknown-pool`, `exit-code-taken`, `unknown-kind`, `unknown-column-type`, `bad-heartbeat`, `doctype-present`, `row-outside-block`, `duplicate-block-id`, `bad-number-format`, `wit-version-mismatch` | workload v1, WIT `wdf:task@1.0.0` |
| #10, #6 | Runtime library and pins | `packages/runtime/` (`wdf_runtime`) | `command`, `warm_up`, `classify`, `pins`, `python -m wdf_runtime run`, `python -m wdf_runtime.calibrate`, `catalogue.yaml` | runtime v1 |
| #10, #24, #6, #15 | Compile library and bundle reader | `packages/compile/` (`wdf_compile`) | `build(bundle, entry, out, find_links=None, timeout_s=None) -> Built`, `bundle.info(path) -> BundleInfo`, `bundle.write(tree, out)`, `python -m wdf_compile build\|info`, `python -m wdf_compile.heavy` | compile v1 |
| #10, #6, #17 | Aggregate library | `packages/aggregate/` (`wdf_aggregate`, distribution `wdf-aggregate`) | `run(...)`, `python -m wdf_aggregate run\|files` | aggregate v1 |
| #17, #14, #15, #16, #18 | Kernel and host checks | `packages/kernel/` (`wdf_kernel`) | `expected_blocks`, `expected_digest`, `expected_summary`, `format`, `writer`, `reader`, `python -m wdf_kernel.check results\|summary` | kernel v1 |
| #17, #15, #18 | Workload library and bundle | `packages/workloads/` (`wdf_workloads`) | `wdf_workloads.main`, `python -m wdf_workloads.bundle` | workloads v1 |
| #10, #2 | Spike results | `docs/delivery/spikes/S1b/heavy-compile.md`, and `docs/delivery/spikes/S6/cold-start.md` (landed) | pass or fail, numbers, decision | none |

## 3. Lanes

Package names are proposals.
Lane 0 owns every package it creates until the owning lane takes over the glob, except the contract modules of `wdf_runtime` and `wdf_kernel.format`, which stay lane 0's.

Departures from the method, stated so the captain approves them knowingly:

- The spike S1b runs beside lane 0, because it writes only under `docs/delivery/spikes/`, which the method allows alongside the active epic.
  It gates only C.2, which is filed after its result merges.
  S6 has landed already (decision 21), so this spec files no S6 issue.
- Lane Needs run across lanes where a test over a stub would assert only `NotImplementedError`: a real guest run needs the compile library (C.1) and the runtime library (R.1), so the level 0 and level 1 guest tests wait for both.
- `docs/delivery/method.md` is not edited by this spec; its verifier table and E7 row still list `verify-workloads`, a follow-up that the earlier specs record the same way.

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Contract and skeletons | 1 | `contracts/workload/**`, `packages/runtime/src/wdf_runtime/{__init__,models,contract,gen}.py`, `packages/runtime/tests/test_{loader,drift,stubs}.py`, `packages/kernel/src/wdf_kernel/format.py`, `packages/kernel/tests/test_format.py`, the skeletons of `packages/{kernel,workloads,compile,runtime,aggregate}/**` until their lanes take over, the root `pyproject.toml` edit, the `slow` marker line of `scripts/test.sh`, `uv.lock` for `componentize-py`, one row of `docs/contracts.md`, `docs/architecture/15-contracts.drawio` and its svg, `docs/architecture/14-wasm-workload-library.drawio` and its svg | 0.1, 0.2 | nothing (barrier, merged alone) |
| S Spikes | 1 | `docs/delivery/spikes/S1b/**`, one row in `docs/delivery/spikes/README.md` | S1b | nothing, beside lane 0 |
| K Kernel and formats | 1 (a second worker can take K.2 beside K.1, the files are disjoint) | `packages/kernel/**` except lane 0's files | K.1, K.2, K.3 | lane 0 |
| W Workloads | 1 | `packages/workloads/**` | W.1, W.2, W.3 | K.1, K.2, C.1, R.1 |
| C Compile | 1 | `packages/compile/**` | C.1, C.2 | lane 0, S1b for C.2 |
| R Runtime | 1 | `packages/runtime/**` except lane 0's files | R.1, R.2, R.3 | lane 0, C.1 for R.1's guest half, W.3 for R.2, W.2 for R.3 |
| A Aggregate | 1 | `packages/aggregate/**` | A.1, A.2 | lane 0, K.2, K.3 for A.2 |

Shared-file rules:

- `uv.lock` is the one hotspot.
  Lane 0 commits `componentize-py==0.25.1` for `wdf-compile`, and A.1 commits `pandas` and `numpy` for `wdf-aggregate`.
  A lock conflict is never resolved by hand, and `uv lock` is rerun after rebase.
- `packages/runtime/src/wdf_runtime/__init__.py` and each package's `tests/conftest.py` are lane 0's, and a lane imports by module path.
- `contracts/workload/README.md` is lane 0's, and after lane 0 merges a pinned file changes only through a contract-change PR.
- `docs/delivery/spikes/README.md` gets one appended row for S1b, and its S6 row exists.
- `docs/architecture/14-wasm-workload-library.drawio` and its svg are refined by 0.1 before any other lane starts (five-input key, `results.xml` for both levels, no cache step inside the build library), so a worker reading the architecture reads decisions 5, 6 and 17.
  The exit-test worker re-checks them at closure.
- `deploy/**`, `justfile`, `just/*.just`, `.github/workflows/*`, `nix/**` and `deploy/versions.yaml` are not touched, and `scripts/test.sh` changes only in 0.2's marker line.
- Generated schemas are committed, regenerated by `python -m wdf_runtime.gen`, and never hand edited.
- No tracked file holds a credential.

## 4. Issues

Each issue below was filed on 2026-11-09 from the [lane issue template](../../../.github/ISSUE_TEMPLATE/lane-issue.md), as a sub-issue of #11, and its body holds the block that stood here: scope, owns, consumes and produces, acceptance criteria, verifier, and needs and blocks as links.
Done means, for every issue: `just ci` is green on the branch, the output of the named tests is pasted in the PR, and nothing outside Owns and the shared files changed.
Commands are written without the `nix develop -c uv run --frozen` prefix that every Python command takes.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 ADR: toolchain, runtime and WASI pin | 0.2 (`componentize-py` pin), R.1 (`pins`), the README of 0.1. `wasmtime` and `wasm-tools` are delivered |
| 2 Guest contract spec | 0.1, R.1 |
| 3 Deterministic kernel | K.1 |
| 4 Level 0 and level 1 workloads | W.1, W.2 |
| 5 `paced_sim` | deferred to #18 (decision 13) |
| 6 `fault` | W.3 |
| 7 Aggregate task library | A.1 |
| 8 Compile task | C.1 |
| 9 Runtime library | R.1, and S6 (landed, decision 21) |
| 10 Calibration | R.3 |
| 11 Wasmtime behaviour pin tests | R.2 |
| 12 Kill-and-resume | deferred to #18 (decision 13) |
| 13 Rust spike | not filed (D1) |
| 14 WIT contract and conformance check | 0.1, C.1 |
| 15 Heavy-compile bundles and compile limit tests | S1b, C.2 |
| 16 XML result format and the streaming writer | 0.1, K.2 |
| 17 Aggregate image | decision 18 (#10), A.2 (hardening tests) |
| 18 Workload ladder fixtures | 0.1 (level 0), K.3 (level 1 and summaries) |

### Filed issues

- [#272](https://github.com/AndoNorth/wasm-dag-forge/issues/272) **0.1 Workload contract** Lane 0, tier T0. Needs: nothing. Blocks: every other issue but the spikes.
  Its Owns also holds the scaffolding its modules need to import and pass their tests: `packages/{runtime,kernel}/pyproject.toml`, `packages/runtime/src/wdf_runtime/__init__.py`, `packages/kernel/src/wdf_kernel/__init__.py`, the package data links under `packages/runtime/src/wdf_runtime/contract/`, the root `pyproject.toml` workspace sources and dev group entries for both packages, and their `uv.lock` workspace entries.
  The rest of the skeletons stays with 0.2.
- [#273](https://github.com/AndoNorth/wasm-dag-forge/issues/273) **0.2 Package skeletons and the toolchain pin** Lane 0, tier T0, and T1 for the wheel test. Needs: 0.1. Blocks: every code issue.
- [#274](https://github.com/AndoNorth/wasm-dag-forge/issues/274) **S1b Heavy compile spike** Lane S, spike, timebox 1.5 days, throwaway code in `docs/delivery/spikes/S1b/code/`. Needs: nothing. Blocks: C.2.
- [#275](https://github.com/AndoNorth/wasm-dag-forge/issues/275) **K.1 Deterministic kernel** Lane K, tier T0. Needs: lane 0. Blocks: K.3, W.1.
- [#276](https://github.com/AndoNorth/wasm-dag-forge/issues/276) **K.2 Result format writer, reader and host check** Lane K, tier T0. Needs: lane 0. Blocks: W.1, A.1.
- [#277](https://github.com/AndoNorth/wasm-dag-forge/issues/277) **K.3 Expected summaries and level 1 fixtures** Lane K, tier T0. Needs: K.1, K.2. Blocks: A.2, R.3.
- [#278](https://github.com/AndoNorth/wasm-dag-forge/issues/278) **C.1 Compile library and bundle reader** Lane C, tier T1 (real `componentize-py` and `wasm-tools` on the host). Needs: lane 0. Blocks: C.2, R.1 for the guest half, W.1.
- [#279](https://github.com/AndoNorth/wasm-dag-forge/issues/279) **C.2 Compile limits and heavy-compile bundles** Lane C, tier T1. Needs: C.1, S1b.
- [#280](https://github.com/AndoNorth/wasm-dag-forge/issues/280) **R.1 Runtime library** Lane R, tier T0 for the table, T1 for the guest half. Needs: lane 0, C.1 for the guest half. Blocks: W.1, R.2, R.3.
- [#284](https://github.com/AndoNorth/wasm-dag-forge/issues/284) **R.2 Wasmtime behaviour pin tests** Lane R, tier T1. Needs: R.1, W.3.
- [#285](https://github.com/AndoNorth/wasm-dag-forge/issues/285) **R.3 Calibration and catalogue** Lane R, tier T1. Needs: R.1, W.2, K.3.
- [#281](https://github.com/AndoNorth/wasm-dag-forge/issues/281) **W.1 Level 0 workloads, entry module and bundle** Lane W, tier T1. Needs: K.1, K.2, C.1, R.1. Blocks: W.2, W.3.
- [#282](https://github.com/AndoNorth/wasm-dag-forge/issues/282) **W.2 Level 1 workloads** Lane W, tier T1. Needs: W.1. Blocks: R.3.
- [#283](https://github.com/AndoNorth/wasm-dag-forge/issues/283) **W.3 Fault workload** Lane W, tier T1. Needs: W.1. Blocks: R.2.
- [#286](https://github.com/AndoNorth/wasm-dag-forge/issues/286) **A.1 Aggregate library** Lane A, tier T0 for the reduce, T1 for the store (marked `integration`, on `store_session`). Needs: lane 0, K.2. Blocks: A.2.
- [#287](https://github.com/AndoNorth/wasm-dag-forge/issues/287) **A.2 Aggregate hardening and memory** Lane A, tier T0. Needs: A.1, K.3.

## 5. Order, stubs and spikes

- First, in parallel: 0.1 and S1b.
  Then 0.2.
  Lane 0 (0.1, 0.2) merges alone, with an estimate of three days.
- After lane 0: K.1, K.2, C.1 and A.1 start together (A.1 waits for K.2 before its reader tests merge).
- After C.1: R.1. After K.1, K.2, C.1 and R.1: W.1. After W.1: W.2 and W.3. After S1b and C.1: C.2. After R.1 and W.3: R.2. After R.1, W.2 and K.3: R.3. After K.1 and K.2: K.3. After A.1 and K.3: A.2.
- DAG: (0.1 ‖ S1b), then 0.2, then (K.1 ‖ K.2 ‖ C.1 ‖ A.1), then (R.1 ‖ K.3 ‖ C.2), then W.1, then (W.2 ‖ W.3 ‖ A.2), then (R.2 ‖ R.3), then the exit test.
- No issue needs the cluster.
  T1 work uses the host's `componentize-py` and `wasmtime`, and only A.1 starts a container (one SeaweedFS through `store_session`).

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| The skeleton entry points of 0.2 | `NotImplementedError("not implemented: owned by lane <letter>")` | each package's `test_stubs.py` accepts the error only for names in `STUBBED`, and the owning issue empties the list |
| The stand-in runner of `wdf_artifactclient.testing` | a Python callable as the guest | `RUNNERS` takes only Python callables, so R.1's `test_real_guest_outputs_meet_staging` runs a real component in a `make_scratch` directory and checks its outputs against the staging contract; #10's runner tests stage and collect around a real pod |
| Params passed directly to the libraries | the activity payload of #6 | #6 pins the payload and its workflow test calls the same libraries |
| The `cache_key` in `build-info.json` | the workflow's lookup and record | #6 refuses to record an entry whose `build-info.json` carries another key than the one it computed, and its cache test shows the refusal |

Spikes: S1b, as its issue block in section 4 states, with a binary pass line, runs beside lane 0 and gates C.2.
S6 has landed (decision 21).

## 6. Verifier delta

None, per decision 16: no skill, no feature file, no verify recipe.
Each issue names its tests in section 4, and the exit test in section 7 proves the epic from a clean export.
Decision 16 rejected the alternative, under which lane 0 would have gained `.claude/skills/verify-workloads/` with the features `compile` (T1, scripted by C.1), `run-level0` (T1, W.1), `run-level1` (T1, W.2), `fault-classes` (T1, W.3), `aggregate` (T1, A.1) and `calibrate` (T1, R.3), and `pause-resume` stays `planned: needs #18`.

## 7. Exit test

Run by a worker who did not write the lanes, on a clean `git archive` export of `main` taken outside any git repository, with Docker running for the whole run, since rows 9, 11 and 16 start containers.
No cluster is started and the cluster lock is not taken.
Each command runs from the export root, its real output is pasted in the PR that records the run, and it is summarised in Closure.
Every command takes `nix develop -c`, and every Python command takes `uv run --frozen`.
`$W` is a scratch directory outside the export.

| # | Command | Expected |
| --- | --- | --- |
| 1 | `uv sync --frozen` then `uv lock --check` | exit 0 for both, lock unchanged |
| 2 | `for m in wdf_runtime.gen wdf_events.schemagen wdf_jobdoc.schemagen wdf_artifactclient.gen; do python -m $m --check \|\| echo "DRIFT $m"; done` | no output |
| 3 | `for f in contracts/workload/vectors/*/valid/*; do contracts/workload/check.sh "$f"; done`, the same over every file under `contracts/workload/vectors/*/invalid/` | exit 0 for each valid file, exit 1 with the `reason:` of its directory for each invalid one |
| 4 | `scripts/check-contracts-map.sh`, `scripts/export-diagrams.sh --check`, `wasm-tools component wit contracts/workload/wit` | exit 0 for each, the last printing world `task` |
| 5 | `python -m wdf_workloads.bundle --out $W/b`, `python -m wdf_compile info $W/b/bundle.tar`, then `python -m wdf_compile build --bundle $W/b/bundle.tar --entry wdf_workloads.main --out $W/c1` and the same into `$W/c2` | exit 0 for each, `info` lists `wdf_workloads.main` among the modules, both builds print the same `cache_key`, and the two component hashes are recorded |
| 6 | `wasm-tools component targets contracts/workload/wit $W/c1/component.wasm`, and `contracts/workload/check.sh $W/c1/build-info.json` | exit 0 for both |
| 7 | for each of `echo`, `sum_squares`, `hash_chain`: `python -m wdf_runtime run --component $W/c1/component.wasm --input contracts/workload/vectors/level0/<w>/input.json --out $W/r0-<w>`, then `python -m wdf_kernel.check results --input contracts/workload/vectors/level0/<w>/input.json --results $W/r0-<w>/results.xml` | `class: ok` and exit 0 for each run, and exit 0 for each check |
| 8 | the same for `noop`, `cpu_burn`, `mem_hold` and `io_churn` with the inputs `packages/kernel/src/wdf_kernel/fixtures/level1/<w>/input.json` of K.3, run once with `$W/c1` into `$W/r1-<w>` and once with `$W/c2` | `class: ok`, exit 0 for each check, and the same `digest:` for both components of each workload |
| 9 | `pytest packages/kernel packages/workloads packages/compile packages/runtime packages/aggregate -q -rs` | all pass with no `SKIPPED` line |
| 10 | `pytest packages/kernel packages/workloads packages/compile packages/runtime packages/aggregate -v -k "every_fault_mode_has_its_class or every_class_row_has_a_case or compile_failed_cases or locked_dependency_install or timeout or dependency_unavailable or source_build_refused or no_network_step or rebuild_same_key or input_copy_rehashed or cap_below_minimum_refused or warm_up_fills_module_cache or real_guest_outputs_meet_staging or over_cap_result_is_guest_failed or pin or output_bytes_independent_of_arrival_order or input_failures_classified or entity_expansion or doctype or malformed_block or no_import_inside_functions or bundle_info_meets_resolver_contract"` | each named test is listed `PASSED` |
| 11 | `pytest packages/aggregate -v -k run_through_store_index` | `PASSED`, with one SeaweedFS container started by `wdf_itest` and removed after |
| 12 | `pytest packages/kernel packages/workloads packages/aggregate -v -m slow -k "million or cap_sized"` | `test_million_rows_round_trip`, `test_io_churn_million_rows_in_guest` and `test_cap_sized_result_peak` pass, and the peak memory printed is below 1,024 MiB |
| 13 | `python -m wdf_aggregate files --aggregation summary_stats --args '{}' --out $W/s1 t1=$W/r1-cpu_burn/results.xml t2=$W/r1-io_churn/results.xml`, the same with the two arguments reversed into `$W/s2`, `sha256sum $W/s1/summary.json $W/s2/summary.json`, then `python -m wdf_kernel.check summary --summary $W/s1/summary.json --inputs t1=<cpu_burn input.json> t2=<io_churn input.json>` | exit 0 for each, equal hashes, check exit 0. `$W/r1-*` are the outputs of row 8 |
| 14 | `python -m wdf_runtime.calibrate --component $W/c1/component.wasm --runs 3` | exit 0 and `units_per_second` printed, recorded in Closure |
| 15 | `pytest packages/kernel packages/workloads packages/compile packages/runtime packages/aggregate -q -rs -k wheel` | all pass, none skipped |
| 16 | `just ci` | exit 0 |
| 17 | the `ci` workflow on `main` | one green run, its URL recorded in Closure |

Not provable by this epic: a compile, a simulate or an aggregate inside a pod (#10), a compile OOM at the pod limit on the cluster and the mirror serving a compile pod (#10, #15; S1b measures the limit in a container), exit 137 decided with node events (#10), the pool images, their read-only root, non-root user and lack of egress (#10, per decision 18), the workflow computing and using the cache key and mapping the exit classes (#6), heartbeats reaching Temporal (#10), `paced_sim`, checkpoints and kill-and-resume (#18), aggregate over 100,000 results with real store latency (#16), cold-start behaviour of many simulate pods at once on the cluster and the warm-up rule under the simulate slot cap (#10, beyond S6's single container), and a second source language.

## 8. Parallelism and cost

- Maximum parallel workers: 2 for the first phase (0.1 and S1b), then 1 for 0.2, then 4 (K.1, K.2, C.1, A.1), then 3 (R.1, K.3, C.2), then 1 to 3 (W.1, then W.2, W.3, A.2), then 2 (R.2, R.3), then 1 for the exit test.
- Host constraints: T1 work runs `componentize-py` (about 0.5 GiB and 3 s per build, measured by the epic) and `wasmtime` on the host.
  A pytest session builds the workload component once and caches it, so `just test` pays one build per session plus one cold `wasmtime` compile.
  Only A.1's store tests and S1b start containers, at most one each, and S1b caps its own at the slot size, which matters on a 20 GiB host shared with other containers.
  Nothing takes the cluster lock.
- CI cost, estimates: no new workflow, no new image, one component build per CI session, and the large-result tests marked `slow` run only in the exit test, because `scripts/test.sh` deselects them (0.2).
- Estimates, not measurements, in worker-days: lane 0 (3: 0.1 2, 0.2 1), S1b (1.5), K.1 (2), K.2 (2.5), K.3 (1.5), C.1 (3), C.2 (2), R.1 (2.5), R.2 (2.5), R.3 (1.5), W.1 (2), W.2 (3), W.3 (1.5), A.1 (3), A.2 (2), exit test (1.5).
  That is about 35 worker-days of effort and about 17 days elapsed along lane 0, C.1, R.1, W.1, W.2, R.3 and the exit test (3 + 3 + 2.5 + 2 + 3 + 1.5 + 1.5 = 16.5), with the real component outgrowing S6's 58 MiB of first-run headroom, `uv.lock` rebases and slow component builds as the main sources of slip.
  The rejected alternatives of decisions 16 and 18 would have added about 3 worker-days each.

## 9. Closure (filled at close)

Commit sha, the exit test run, CI run URL, what was learned, what moved, and `docs/architecture/14-wasm-workload-library.drawio` as 0.1 refined it, re-checked against what was learned.

Closure addenda, one dated line each, added after close when a `revisit` child issue is filed against this epic:

- none yet

---
