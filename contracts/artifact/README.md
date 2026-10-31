# Artifact contract

Owner epic: #4 (job event envelope and state propagation).
Version: 1
Consumers: storage (#3), workers (#10), toolchain (#11) and artifacts (#12).

An artifact reference points to an object and holds no bytes.
This directory pins the reference shape, the four buckets and the key template of each kind.
The semantics of integrity checks, retention and the reaper belong to the artifacts epic (#12), and the object store itself to the storage epic (#3).
The reference schema is [reference.schema.json](reference.schema.json), and the layout is [layout.yaml](layout.yaml), validated by [layout.schema.json](layout.schema.json).

## Reference

A reference has `uri`, `sha256`, `size`, `name` and `kind`, and a `producer` of job, task and attempt for the attempt-scoped kinds only.
`uri` is `s3://<bucket>/<key>`, `sha256` is 64 lower-case hex digits of the object bytes, and `size` is the object size in bytes.
`producer.attempt` is an attempt identity as the key formulas of `contracts/events/` define it.
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
An attempt-scoped key names the tenant, batch, job, seed, task and attempt, and a checkpoint adds a segment, so an attempt identity that is never reused never collides.
A summary of an aggregate task sits under `<tenant>/<batch>/aggregate/<job>/<task>/<attempt>/<name>`.
The file `build-info.json` and its schema belong to the toolchain epic (#11), and this contract pins only the reference of a `build-info` object and its bucket.

## Loader

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

## Vectors and the loader test

`vectors/valid/*.yaml` must pass, and `vectors/invalid/<reason>/*.yaml` must fail with that reason.
`scripts/verify/cluster/lib/vectors.sh contracts/artifact` runs them.
`packages/events/tests/test_contract_vectors.py` runs the same loader test inside `just ci`, and checks the buckets and key templates against those of the storage epic.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
