# Events contract

Owner epic: #4 (job event envelope and state propagation).
Version: 1
Consumers: every epic.
Storage (#3), validator (#5), workflow (#6), broker (#7), admission (#8), placement (#9), workers (#10), toolchain (#11), artifacts (#12), observability (#13), dispatch (#24) and the checkpoint epic (#18) read it.

Every platform event is a CloudEvent 1.0 in structured mode with the JSON event format, and the same bytes travel on every hop.
This directory pins the profile, the schema of each data type, the vectors and the key formulas.
The code lives in `packages/events/` (import `wdf_events`) and `packages/keys/` (import `wdf_keys`).

## Profile

[profile/profile.schema.json](profile/profile.schema.json) holds the platform rules on top of the published CloudEvents schema, which is vendored unchanged as [profile/cloudevents.json](profile/cloudevents.json).
[profile/cloudevents.source.yaml](profile/cloudevents.source.yaml) records its source commit and SHA-256, and a test fails when the file and the record disagree.

| Attribute | Rule |
| --- | --- |
| `specversion` | `1.0` |
| `id` | A string without `#`, derived by `cloudevent_id` below |
| `source` | `/wdf/<component>`, one source per component and never per pod or per tenant |
| `type` | `dev.wdf.<job or task>.<name>.v<major>`, where `dev.wdf` is a placeholder prefix |
| `time` | Required, UTC, exactly six fractional digits and a `Z` suffix |
| `datacontenttype` | `application/json` |
| `dataschema` | `urn:wdf:schema:<type without the prefix and the major>:<major>.<minor>` |
| `subject` | The job id for a job type, and `<job id>/<task id>` for a task type |
| `tenant`, `jobid`, `priorityclass`, `jobclass`, `traceparent` | Required on every event |
| `taskid` | Required on a task type, and the `subject` is built from it |
| `attempt` | An integer of 1 or more, present on events that belong to an attempt |
| `tracestate` | Optional, at most 512 characters |
| `data` | An object that validates against the schema `dataschema` names |

Extension attribute names are lower-case letters and digits within 20 characters, and an attribute outside this table is rejected.
`data_base64` is rejected too, so every event carries its data as JSON.
Sensitive values never go in a context attribute.

### `time`

`time` is the time of the fact being recorded and never a write-time clock.
It always carries exactly six fractional digits, zero padded by producers (`wdf_events.format_time`), so that the event store key `<source>#<id>` plus `time` survives a `timestamptz` round trip and a replay inserts nothing new.

### Content type

The content type of a structured message is `application/cloudevents+json; charset=utf-8`.
A reader compares the media type and the charset case-insensitively, so the `charset=UTF-8` form that the broker spike used is accepted as well.

## Data schemas and the reader rule

`schemas/<type without the prefix and the major>/<major>.<minor>.json` holds one JSON Schema file per type and minor.
The files are generated from the Python models by `python -m wdf_events.schemagen`, checked by `python -m wdf_events.schemagen --check`, and never edited by hand.
`dataschema` is a stable identifier mapped to a file by the path rule above, and it is not a resolvable URL.

A reader resolves an event like this:

1. A `dataschema` that is not well formed is rejected.
1. A type whose major has no schema file is an unsupported major.
1. A `dataschema` that names another type or another major than the `type` is rejected.
1. A minor the reader knows is validated strictly, so an unknown field is rejected.
1. An unknown minor of a known major resolves to the highest minor the reader knows, and added optional fields are ignored.

The type carries only the major.
A compatible change keeps the type and bumps the minor in `dataschema`, and an incompatible change is a new type.

## Keys

The formulas are written here and nowhere else.
`vectors/keys.json` holds inputs and expected outputs only.
Inputs are used verbatim, with no case folding and no trimming.

`frame(domain, field...)` is the concatenation, for the domain and then each field in order, of the number of UTF-8 bytes of the value in decimal, a colon, and the value.
A field boundary therefore cannot move without changing the frame.
`derive(domain, field...)` is the UUID version 5 of namespace `9a3dcb38-1d98-5483-9f76-afc1a01e3f49` (the version 5 UUID of `wdf.dev` in the DNS namespace) over the UTF-8 bytes of `frame(domain, field...)`, written as 36 lower-case characters.

| Function | Formula |
| --- | --- |
| `job_id(tenant, submission_key)` | `derive("job", tenant, submission_key)` |
| `batch_id(tenant, batch_key)` | `derive("batch", tenant, batch_key)` |
| `workflow_id(job_id)` | `"job-" + job_id` |
| `attempt_identity(activity_attempt, resume_counter)` | `"r<resume_counter>a<activity_attempt>"`, and `ValueError` when `activity_attempt` is below 1 or `resume_counter` is below 0 |
| `cloudevent_id(job_id, task_key, attempt_identity, kind, sequence)` | `derive("event", job_id, task_key, attempt_identity, kind, <sequence in decimal>)`, with `task_key` and `attempt_identity` empty where they do not apply, and `ValueError` when `sequence` is below 0 |
| `compile_cache_key(source_hash, lockfile_hash, componentize_py_version, wit_version)` | the lower-case hex SHA-256 of the UTF-8 bytes of `frame("compile", source_hash, lockfile_hash, componentize_py_version, wit_version)` |

The activity attempt restarts after retry exhaustion and a patch, and the resume counter lives in workflow state, so the pair never repeats.
The workflow and the compile worker both import `compile_cache_key` and compute it nowhere else.
`wdf_keys` holds stubs that raise `NotImplementedError("not implemented: owned by lane K")` until the owning lane replaces them, and `packages/keys/tests/test_vectors.py` lists the stubbed names in `STUBBED`.
A vector that the real functions disagree with is fixed by a contract-change PR.

## Loader

`contracts/events/check.sh <event.json>` validates one event against the CloudEvents schema, the profile and its data schema.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.

| Reason | Fails when |
| --- | --- |
| `not-an-object`, `malformed-json` | the file is not a JSON object |
| `cloudevents-schema` | the published CloudEvents schema rejects the event |
| `missing-<attribute>` | a required attribute is absent, for example `missing-time` |
| `bad-<attribute>` | an attribute breaks its profile rule, for example `bad-specversion`, `bad-time`, `bad-traceparent`, `bad-dataschema` and `bad-subject` |
| `type-without-major` | the `type` has no `.v<major>` suffix |
| `id-contains-hash` | the `id` contains `#` |
| `bad-extension-name` | an attribute name has a capital letter or more than 20 characters |
| `unknown-attribute` | an attribute is outside the profile table |
| `unknown-type` | no schema directory exists for the type |
| `unsupported-major` | the type names a major that has no schema file |
| `dataschema-type-mismatch` | the `dataschema` names another type or major than the `type` |
| `bad-data` | the data fails its schema |
| `event-too-large` | the event is over 64 KiB |

The library raises one of three typed errors, each with a `reason` and a `dlq_reason` for the broker dead letter queue.
`UnsupportedMajor` maps to `UNSUPPORTED_VERSION`, and `SchemaInvalid` and `EventTooLarge` map to `MALFORMED`.

## Vectors and the loader test

`vectors/accepted/valid/*.json` must pass, and `vectors/accepted/invalid/<reason>[--<variant>].json` must fail with that reason.
`packages/events/tests/test_profile.py` runs both through the library and through `check.sh`.
`packages/events/tests/test_schema_drift.py` runs the schema drift check, and `packages/keys/tests/test_vectors.py` runs the key vectors.

## Changing it

A change after the pin is a `contract-change` PR and bumps `VERSION`.
A change to a generated schema touches `contracts/`, the model under `packages/` and the regenerated file together, because the drift check fails otherwise.
