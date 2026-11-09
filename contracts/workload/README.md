# Workload contract

Owner epic: #11 (workload toolchain, guest contract and aggregate task).
Version: 1, in [VERSION](VERSION).
WIT contract version: 1.0.0, the version in the `package` line of [wit/task.wit](wit/task.wit).
The [Consumers](#consumers) section names who reads each part.

A guest is a plain WebAssembly command component, built from Python by the compile task and run by the stock `wasmtime` CLI on the simulate pool.
The aggregate task is native Python and not a guest.
This directory pins what crosses between them and the workers: the WIT world, the guest contract, the exit class table, the result format, the summary format, the build info, the bundle format and the level 0 golden vectors.
The schemas are generated from the models of `wdf_runtime.models` and never edited by hand.

## Two versions

`VERSION` versions this directory, and a change to the exit class table, a schema or a vector bumps it.
The WIT contract version versions the guest contract and is an input of the compile cache key (`wdf_keys.compile_cache_key`), so it changes only when the world a guest targets changes.
A change to the exit class table or the result schema therefore never changes a cache key.

## WIT world

[wit/task.wit](wit/task.wit) is `package wdf:task@1.0.0; world task { include wasi:cli/command@0.2.9; }`, and adds nothing to the command world.
The WASI 0.2.9 packages it includes are vendored under `wit/deps/`, copied from the `proposals/<name>/wit/*.wit` files of the `WebAssembly/WASI` v0.2.9 release archive (SHA-256 `8c52624aebed805e29aa1e64053fd3171159bbe8a195e90982f87edae789a6e8`), with trailing whitespace removed from comment lines.
`wasm-tools component wit contracts/workload/wit` prints the resolved world.
The compile task checks every component against this world with `wasm-tools component targets`, and refuses one that imports or exports anything beyond it.
`wdf_runtime` ships the tree as package data, so the compile task reads it without a repository checkout.

## Guest contract

Version 1 of the guest contract is four things, and nothing else is in the world or the contract: no host function, no environment variable and no extra mount.

| Part | Rule |
| --- | --- |
| Input | `/in/input.json`, the canonical JSON (sorted keys, no whitespace, one final line feed) of the bound simulate params, exactly `args`, `seed` and `workload`. Schema [input.schema.json](input.schema.json). `/in` is a fresh directory holding only a copy of the file, mounted with `--dir HOST::/in`, and the runner re-hashes the copy after the run |
| Result files | `/out/results.xml` in `wdf.sim.v1`, the declared output of the simulate task in `contracts/artifact/staging.yaml`, and the optional `/out/error.json` |
| Exit code | 0 when the guest succeeded, nonzero otherwise, classified by the exit class table |
| Heartbeat | stderr lines `HEARTBEAT {"progress":f,"step":n,"steps":N}` |

### Error file

A failing guest may write `/out/error.json`, `{"code": "<slug>", "message": "<text>", "deterministic": true|false}`, at most 4,096 bytes, with schema [error.schema.json](error.schema.json).
`code` is lower case letters, digits and dashes, such as `unknown-workload`.
The staging library skips the file as an undeclared output, and the runner reads it locally and puts `<class>: <code>: <message>` in the failure reason.

### Heartbeat

A heartbeat is one stderr line that matches `HEARTBEAT (\{"progress":[0-9][0-9.e+-]*,"step":(?:0|[1-9][0-9]*),"steps":[1-9][0-9]*\})` in full, compact JSON with the keys in sorted order.
`progress` is a JSON number from 0 to 1, and `step` is at most `steps`.
Any stderr line that starts with `HEARTBEAT` must be a heartbeat, and every other line is free output.
Each level 1 workload emits one at the cadence of its `heartbeat_every` argument, and the aggregate library emits one per input.
The pattern is `wdf_runtime.models.HEARTBEAT`, and `wdf_runtime.contract.heartbeat(line)` parses one line.

## Exit classes

[exit-classes.yaml](exit-classes.yaml) lists the classes of each pool, with schema [exit-classes.schema.json](exit-classes.schema.json).
Each class has a trigger, whether a retry fails the same way, and the exit code of the pool's native library when it has one.
`deterministic` is `true`, `false` or `from-error-file`, which takes `deterministic` from the guest's `error.json` and is `false` when the guest wrote none.

| Pool | Classes |
| --- | --- |
| simulate | `ok`, `timeout`, `guest_memory_cap`, `guest_failed`, `host_error`, `oom_killed` |
| compile | `compile_failed`, `compile_dependency_unavailable`, `compile_timeout`, `compile_oom` |
| aggregate | `aggregate_failed`, `aggregate_input_unavailable`, `aggregate_input_corrupt`, `aggregate_input_refused`, `aggregate_parse_failed`, `aggregate_oom`, `aggregate_timeout` |

Every native library exits 0 when it succeeds, 1 on an unexpected error of its own and 2 on usage, so no class takes 0 or 2.
The aggregate names its exit 1 `aggregate_failed`, and the compile library has no class for its exit 1 in version 1.
The compile codes are 3 `compile_failed`, 4 `compile_dependency_unavailable` and 5 `compile_timeout`.
The aggregate codes are 3 `aggregate_input_unavailable`, 4 `aggregate_input_corrupt`, 5 `aggregate_input_refused` and 6 `aggregate_parse_failed`.
A class without a code is decided by the runner: the simulate classes from `wasmtime`'s exit and stderr, the two OOM classes from exit 137 with node events, and `aggregate_timeout` from the runner's own deadline.
`guest_failed` also covers a zero exit with a `results.xml` above its staging cap, which is deterministic with the reason `guest_failed: output-over-cap: <bytes>`.
The last stderr line of a native library is `reason: <class>: <detail>`.
The workflow maps the classes into its failure class table and does not retry a deterministic one.

## Result format

[results.yaml](results.yaml) is the schema file of `wdf.sim.v1`, with schema [results.schema.json](results.schema.json), and `wdf_kernel.format` holds the value formats and the document rules.
A simulate attempt writes one well-formed UTF-8 `results.xml`.
Its root `results` carries `schema="wdf.sim.v1"` and `seed`, each `block` carries an `id` that is unique in the file and a `kind`, and each `row` inside a block carries exactly the columns of the kind as attributes.
There is no namespace, no `DOCTYPE`, no processing instruction beyond the XML declaration and no text outside an attribute, and a comment is ignored.

| Kind | Columns |
| --- | --- |
| `echo` | `key` str, `value` str holding canonical JSON |
| `scalar` | `name` str, `value` int |
| `digest` | `name` str, `value` hex |
| `series` | `step` int, `value` float |
| `payload` | `step` int, `data` hex |

| Type | Written as |
| --- | --- |
| `int` | decimal, no sign for zero, no leading zeros |
| `float` | Python `repr` of a finite float, so `0.1`, `1e-05` and `1.0` |
| `hex` | lower-case hex of bytes, an even number of digits |
| `str` | the text, with `&`, `<`, `>`, `"`, tab, line feed and carriage return escaped |

The host check compares parsed values and never bytes.
`wdf.sim.v1` changes only by addition, a new kind or a new column, and several blocks share one file.

## Summary format

`summary.json` is the output of an aggregate task, canonical JSON with schema [summary.schema.json](summary.schema.json).
It holds `schema` (`wdf.summary/1`), `aggregation`, `args`, `inputs` (the number of index entries), `missing` (the task ids that did not succeed, sorted) and `rows` (the number of rows read).
For `summary_stats` it adds `blocks.<block id>.<column>` with `count`, `mean`, `std`, `min` and `max` over the numeric columns, where `std` is the sample standard deviation and `null` exactly when `count` is 1.
For `digest_list` it adds `digests`, one `{"task", "seed", "digest"}` per succeeded input in index order.
The aggregations and their arguments are those of `contracts/job/registry.json`.

## Build info

`build-info.json` is the second output of a compile task, with schema [build-info.schema.json](build-info.schema.json).
It holds `schema` (`wdf.build-info/1`), `cache_key`, `inputs` (the five key inputs), `tools` (`componentize_py`, `wasm_tools`, `uv`, `guest_python`), `component` (`sha256`, `size`), `imports` and `exports` as `wasm-tools` reports them, and `timings_s` (`unpack`, `install`, `componentize`, `check`).
`cache_key` is `wdf_keys.compile_cache_key` over `inputs`, and `inputs.wit_version` is the WIT contract version.
It is stored by its own hash, like the component.

## Bundle format

A source bundle is a POSIX tar archive, uncompressed, at most the `source` cap of the staging contract.
Its root holds `pyproject.toml`, `uv.lock` and the source modules, and the modules of a bundle are the dotted names of every `.py` file and package under the root.
The lockfile hash of the cache key is the SHA-256 of the bytes of `uv.lock`.
Unpacking uses `tarfile` with `filter="data"` and refuses links and devices, more than 10,000 entries and more than 256 MiB unpacked.
A bundle builder writes the entries sorted, with zero times and owners, so the same tree gives the same bytes.

## Level 0 golden vectors

`vectors/level0/<workload>/input.json` is the input of a level 0 workload and `expected.json` the parsed values of the `results.xml` it must write.
Each expected value is derived by a formula that a person can check.

| Workload | Expected |
| --- | --- |
| `echo` | one `echo` block `args` with a row per argument, in key order, whose value is the canonical JSON of the argument |
| `sum_squares` | one `scalar` row `sum_squares` of `n(n+1)(2n+1)/6` |
| `hash_chain` | one `digest` row `hash_chain` of `rounds` SHA-256 rounds over the seed as 8 bytes big endian |

`expected.json` is canonical JSON with `schema`, `seed` and `blocks`, each block an `id`, a `kind` and `rows`, with an int or a float as a JSON number and a hex or a str as a JSON string.

## Consumers

| Consumer | Reads |
| --- | --- |
| #6 workflow engine | the exit class table, which it maps into its failure classes, and the WIT version that enters the cache key |
| #10 Kubernetes workers | the guest contract, the heartbeat pattern and the exit class table |
| #13 observability | the exit classes and the heartbeat progress |
| #15 failure scenarios | the exit classes and the malformed block vector |
| #17 test ladder and acceptance | the level 0 golden vectors, the result format and the summary format |
| #18 checkpoint and resume | the guest contract that the checkpoint convention extends |
| #24 dispatch and control API | the bundle format |

## Loader

`contracts/workload/check.sh <file>` runs `python -m wdf_runtime.contract --check <file>`.
A `.yaml` file is `exit-classes.yaml` or `results.yaml`, a `.json` file is an input, error, summary, build info or expected values file, told apart by its `schema` value or its keys, a `.xml` file is a results document and a `.stderr` file is guest standard error.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.
`python -m wdf_runtime.gen [--check]` writes the generated schemas, and with `--check` exits 1 and names each committed schema that differs from the models.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match its model, a canonical file (input, summary, expected values) is not canonical JSON, an error file is above 4,096 bytes, a kind lists a column twice, a pool of the registry has no classes, a summary list is out of order or `std` breaks its rule, the `cache_key` is not the key of the inputs, or a results document breaks a rule that no reason below names |
| `duplicate-class` | a class is listed twice in the table |
| `unknown-pool` | the table names a pool that `contracts/job/registry.json` lacks |
| `exit-code-taken` | a class takes exit 0 or 2, or two classes of a pool take one code |
| `unknown-kind` | a block names a kind that `results.yaml` lacks |
| `unknown-column-type` | a column has a type other than `int`, `float`, `hex` and `str` |
| `bad-heartbeat` | a stderr line starts with `HEARTBEAT` and is not a heartbeat |
| `doctype-present` | a results document declares a `DOCTYPE`, refused before any entity expands |
| `row-outside-block` | a row is a direct child of `results` |
| `duplicate-block-id` | two blocks share an id |
| `bad-number-format` | an `int`, `float` or `hex` value, or the seed, is not written as its type says |
| `wit-version-mismatch` | a build info names another WIT version than `wit/task.wit` |

`vectors/<file>/{valid,invalid/<reason>}/` hold the vectors, and `packages/runtime/tests/test_loader.py` runs them inside `just ci`.
`vectors/results/invalid/schema-violation/malformed-block.xml` is the malformed block fixture, a block that is never closed.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
A change to the WIT world also bumps the version in the `package` line, which changes every compile cache key.
