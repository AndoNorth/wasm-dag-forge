# Artifact contract

Owner epic: #4 (job event envelope and state propagation).
Version: 3
The artifacts epic (#12) adds the failure taxonomy, the staging contract, the object caps, the aggregate index format and the reaper windows.
The [Consumers](#consumers) section names who reads each file.

An artifact reference points to an object and holds no bytes.
This directory pins the reference shape, the four buckets and the key template of each kind.
The semantics of integrity checks, retention and the reaper belong to the artifacts epic (#12), and the object store itself to the storage epic (#3).
The reference schema is [reference.schema.json](reference.schema.json), and the layout is [layout.yaml](layout.yaml), validated by [layout.schema.json](layout.schema.json).

## Reference

A reference has `uri`, `sha256`, `size`, `name` and `kind`, and a `producer` of job, task and attempt for the attempt-scoped kinds only.
`uri` is `s3://<bucket>/<key>`, `sha256` is 64 lower-case hex digits of the object bytes, and `size` is the object size in bytes.
`producer.attempt` is an attempt identity as the key formulas of `contracts/events/` define it, built from the task attempt that the workflow counts across retries, resumes and patches.
A reference has no field beyond these, so none can hold bytes, and the consumer verifies the hash and the size against the object.

## Buckets and addressing

| Bucket | Class | Kinds |
| --- | --- | --- |
| `wdf-sources` | content-addressed | `manifest`, `source`, `index` |
| `wdf-components` | content-addressed | `component`, `build-info` |
| `wdf-results` | attempt-scoped | `result`, `output`, `log-tail`, `summary` |
| `wdf-checkpoints` | attempt-scoped | `checkpoint` |

The key templates are in `layout.yaml` and match the bucket layout of the storage epic.
A content-addressed key is `sha256/<first two hex digits of the hash>/<hash>`, so identical bytes are stored once and a changed object is a new key.
An attempt-scoped key names the tenant, batch, job, seed, task and attempt, and a checkpoint adds a segment, so two runs of one task never write the same key, because an attempt identity is never reused and a store never deduplicates one run against another.
A summary of an aggregate task sits under `<tenant>/<batch>/aggregate/<job>/<task>/<attempt>/<name>`.
The file `build-info.json` and its schema belong to the toolchain epic (#11), and this contract pins only the reference of a `build-info` object and its bucket.

## Reference loader

`contracts/artifact/check.sh <file>` validates one reference against the schema and checks that its `uri` is a key of the template of its kind.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.

| Reason | Fails when |
| --- | --- |
| `unknown-field` | the file has a field that a reference does not have, such as one that holds bytes |
| `schema-violation` | the file does not match the schema, which includes a producer on a content-addressed kind and none on an attempt-scoped one |
| `wrong-bucket` | the `uri` names another bucket than the kind's |
| `bad-hash-prefix` | a content-addressed key has a prefix that is not the first two hex digits of the hash |
| `hash-mismatch` | a content-addressed key does not end in the `sha256` of the reference |
| `missing-attempt-segment` | an attempt-scoped key has no segment for the attempt of the producer |
| `key-template-mismatch` | any other difference between the key and the template, including a producer or a name that the key repeats wrongly |

## Failure taxonomy

[failures.yaml](failures.yaml) lists every artifact failure with its cause, retry rule, attempt class and surface, and [failures.schema.json](failures.schema.json) is generated from the models of `wdf_artifactclient`.
The retry rule is spent inside the client, and the attempt class is what the failed attempt reports.
The surface is the typed exception of the client and the prefix of the reason of a `task.attempt.failed` event, which is the code and a colon.
The `Reason` model of the events contract takes free text, so no event schema changes.

| Retry rule | Meaning |
| --- | --- |
| `backoff-within-deadline` | the client retries with backoff until the deadline of the call, then the attempt fails |
| `refetch-once` | the client reads the object once more, then raises `ARTIFACT_LOST` carrying the first cause |
| `fallback-to-previous-checkpoint` | the client does not retry, and the workflow starts a new attempt from the previous checkpoint |
| `none` | no retry |

`CHECKPOINT_CORRUPT` is a hash or size mismatch or a missing object of a checkpoint, and every other store fault of a verification is `STORE_UNAVAILABLE`.
`ARTIFACT_LOST` is also what `CHECKPOINT_CORRUPT` becomes when no earlier checkpoint verifies.
The class names `retryable` and `non-retryable` are the artifact contract's, and the workflow epic (#6) maps them into its own failure class table.

## Staging

[staging.yaml](staging.yaml) pins the scratch directory of a task and its declared outputs, and [staging.schema.json](staging.schema.json) is generated from the models.
The scratch directory holds `input/`, read only once staged, with `input.json` written by the worker, and `output/`, which the guest writes.
Each task type declares its outputs as a file name, a kind and a cap.
The declared outputs of each task type equal those of `contracts/job/registry.json`, which stays the source, and the loader refuses a difference.
A declared output that is absent or not a regular file fails with `OUTPUT_MISSING`.
An undeclared regular file is not uploaded and is listed in the report and in the log tail, and a symbolic link or a directory is ignored and listed, so a temporary file of a guest never fails an attempt.
The last bytes of the combined standard output and error, up to the cap of `log_tail`, are uploaded as kind `log-tail` even when the attempt failed.

## Limits

`kind_caps` of [staging.yaml](staging.yaml) holds the largest size of one object of each kind, in bytes, and every kind of `layout.yaml` has one.
The caps are estimates chosen against the store claim, and a change is a contract-change PR.
There is no cap for the bytes of a whole job.
A checkpoint cap of 64 MiB with two kept checkpoints for each of 32 parallel tasks is 4,096 MiB, which fits the claim of 10,240 MiB of the store.
The cap of an output of a task type may be lower than the cap of its kind and never higher.

## Aggregate index

An aggregate task that reads many results opens one object of kind `index`, content-addressed in `wdf-sources`, and never lists a bucket.
The object is canonical JSON Lines: keys sorted, no whitespace, each line ended by a line feed.
The same entries in the same order therefore give the same bytes and the same hash.
[index.schema.json](index.schema.json) is generated from the models and describes the two shapes of line.

| Line | Fields |
| --- | --- |
| 1, the header | `schema` (`wdf.artifact.index/1`), `job` and `task` of the aggregate task that reads the index, `tenant`, and `partial` as `refuse` or `allow` |
| after it, one entry per input | `task` (the task id of the input), `status` as `succeeded`, `failed` or `missing`, and `reference` |

Entries are in ascending task id order with no id repeated, which lets a builder write them from a sorted iterator without holding the list.
`reference` is the reference of a result and is present exactly when the status is `succeeded`.
A reader refuses an entry that did not succeed unless the header says `allow`, and verifies each listed result through the client.
A reader also refuses a result whose key does not start with the `tenant` of the header, because an index supplied by a submitter must not read the results of another tenant through the shared `aggregate` credential.
The loader checks the shape of the object and leaves both reader rules to the reader, which needs the store.

## Retention windows

[retention.yaml](retention.yaml) holds the windows of the artifact reaper, and [retention.schema.json](retention.schema.json) is generated from the models.
A window is a number of days from the `terminal_at` of the job, with one entry per terminal status of `contracts/status/status.yaml` for results and for checkpoints.

| Status | Results | Checkpoints |
| --- | --- | --- |
| `succeeded` | 7 days | 1 day |
| `partial`, `failed` | 14 days | 3 days |
| `cancelled`, `terminated` | 3 days | 1 day |

A paused job is not terminal and has no entry, so it keeps its objects until the safety net of `contracts/state/retention.yaml` removes them, and a pause beyond the net is outside the proof of concept.
`checkpoint_keep` is the number of newest checkpoints that a prune keeps for a running job.
`cache_unused_days` is how long a compile cache entry may stay unused, and is twice the maximum job duration, so the entry of a running job never expires.
`content` holds the three intervals of the collection of content-addressed blobs, which no job window governs.
The loader reads the safety net and the terminal statuses through package data and refuses a window that breaks the rules of the reasons below.

## Consumers

| Consumer | Reads |
| --- | --- |
| #3 durable state and storage | the layout, the kind caps, the reaper windows that stay below its safety net, and the failure inputs that it counts |
| #4 job event envelope | the reference shape and the layout, and the failure reasons that `task.attempt.failed` carries |
| #5 DAG configuration and validator | the declared outputs, which equal the registry that the validator reads, and the index format that an aggregate-only job submits |
| #6 workflow engine | the failure taxonomy, which it maps into its failure classes, and the index format that its index activity writes |
| #10 Kubernetes workers | the staging contract and the caps |
| #11 toolchain and guest contract | the file names of the declared outputs |
| #13 observability | the failure codes that become metrics and alerts |
| #15 failure scenarios | the failure codes that its scenarios provoke |
| #16 characterization and soak | the caps and the failure codes that the soak observes |
| #17 test ladder and acceptance | the caps and the failure codes that the acceptance run checks |
| #18 checkpoint and resume | the checkpoint cap and `CHECKPOINT_CORRUPT` |
| #24 dispatch and control API | the caps of the manifest, the source bundle and the index, and the index format that it verifies for a submitter supplied index |

## Contract files and their loader

`python -m wdf_artifactclient.contract --check <failures.yaml|staging.yaml|retention.yaml|index.jsonl>` validates one file against the models, the layout, the registry, the status contract and the safety net.
A file that ends in `.jsonl` is an aggregate index object, and any other file is one of the YAML files.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.
`python -m wdf_artifactclient.gen [--check]` writes the generated schemas, and with `--check` exits 1 and names each committed schema that differs from the models.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match its model, or an event reason prefix is not its code and a colon, or a task type declares a file name twice, or a terminal status has no window, or an index line is not valid canonical JSON of its shape or lacks its final line feed |
| `duplicate-code` | a code is listed twice |
| `unknown-code` | a code that `escalates_to` or `missing_output` names is not in the taxonomy |
| `unknown-kind` | a cap or an output names a kind that `layout.yaml` lacks |
| `kind-without-cap` | a kind of `layout.yaml` has no entry in `kind_caps` |
| `cap-above-kind-cap` | an output or the log tail has a cap above the cap of its kind |
| `unknown-task-type` | a task type is not in the registry |
| `output-not-in-registry` | the declared outputs of a task type differ from those of the registry, or the registry has a type that staging lacks |
| `window-above-safety-net` | a window keeps results or checkpoints longer than the safety net of `contracts/state/retention.yaml` for their bucket |
| `cache-window-below-twice-max-job-duration` | `cache_unused_days` is below twice `MAX_JOB_DURATION_DAYS` of `wdf_compat.window` |
| `keep-below-two` | `checkpoint_keep` is below two |
| `failed-shorter-than-succeeded` | a failed or partial window is shorter than the succeeded one |
| `unknown-status` | a window names a status that is not terminal in `contracts/status/status.yaml` |
| `entry-without-reference` | an index entry that succeeded has no reference |
| `reference-with-failure-status` | an index entry that failed or is missing has a reference |
| `wrong-kind` | the reference of an index entry is not of kind `result` |
| `header-not-first` | the first line of an index object is not the header, or the header appears again, or there is no header |
| `out-of-order` | an index entry has a task id that does not come after the one before it |

`vectors/{failures,staging,retention,index}/{valid,invalid/<reason>}/` hold the vectors, and `packages/artifactclient/tests/test_loader.py` runs them inside `just ci`.

## Vectors and the reference loader test

`vectors/valid/*.yaml` must pass, and `vectors/invalid/<reason>/*.yaml` must fail with that reason.
`scripts/verify/cluster/lib/vectors.sh contracts/artifact` runs them.
`packages/events/tests/test_contract_vectors.py` runs the same loader test inside `just ci`, and checks the buckets and key templates against those of the storage epic.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
