# Stages contract

Owner epic: #4 (job event envelope and state propagation).
Version: 1
Consumers: storage (#3), workflow (#6), admission (#8), placement (#9), workers (#10) and dispatch (#24).

The stage table says, for each stage of the platform, which source it writes under, which context attributes and data fields it reads, and which event types it emits.
The table is [stages.yaml](stages.yaml), validated by [stages.schema.json](stages.schema.json) and against the event catalogue by the loader.
Event types are named without the `dev.wdf` prefix and the major, as the schema directories of [the events contract](../events/README.md) are.

## Stages

| Stage | Source | Emits |
| --- | --- | --- |
| `dispatch` | `/wdf/dispatch` | `job.accepted` and `job.published` |
| `control` | `/wdf/control` | the four request types, and `job.paused`, `job.resumed`, `job.cancelled` and `job.terminated` for a job that never started |
| `admission` | `/wdf/admission` | `job.admitted`, and `job.dead.lettered` for a message that parses far enough to name a job |
| `workflow` | `/wdf/workflow` | every other job type and every task type except the task request |
| `placement` | `/wdf/placement` | nothing, it reads the task type, CPU and memory of the task request |
| `worker` | `/wdf/worker` | nothing, it reads the task request and writes references into heartbeat details |

The workflow builds the task request and hands it to the activity.
It never writes the task request to the event history, which is why it is under `builds` and not under `emits`, and why no stage has to emit it.

## Shape

Each stage has `source`, `reads` (`attributes` and `data`, where `data` maps an event type to the fields read), `emits`, and optionally `builds` and `writes`.
An `emits` entry has the `type`, the `source` it is written under, and an optional `when`.
The `source` of an entry is repeated from its stage on purpose, so that an entry moved between stages without being edited fails the loader.

A type that two stages emit needs a `when` on each entry, which names the condition under which that stage is the emitter.
The four types that a job without a workflow gets from the control stage are the only ones today.
The projection in [the status table](../status/status.yaml) keys the same cases by source.

## Obligations of the stage epics

The text of the stage epics does not list these yet, and each confirms them in its own spec.

- The dispatch epic (#24) emits `job.published` and `job.terminate.requested`.
  It also emits `job.cancelled`, `job.terminated`, `job.paused` and `job.resumed` for a job with no workflow, and a `job.resumed` there returns the row to `accepted`.
- A dead letter made by the broker itself (a delivery limit or an expiry) has no emitter here and stays with the reporter question of the broker epic (#7).
  So does a message with no derivable job id, because no job-level `job.dead.lettered` is built for it.

## Loader

`contracts/stages/check.sh <file>` validates a table.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match the schema, which includes a missing stage and a task request under `emits` |
| `duplicate-emitter` | two stages emit a type and an entry lacks a `when` |
| `unemitted-type` | a catalogue type other than the task request is emitted by no stage |
| `source-mismatch` | an emitted event names a `source` other than the stage's own |
| `unknown-attribute` | a stage reads an attribute that the event profile does not have |
| `unknown-data-field` | a stage reads a data field that the type's schema does not have |
| `unknown-type` | a stage reads or emits a type that is not in the catalogue |

## Vectors and the loader test

`vectors/valid/*.yaml` must pass, and `vectors/invalid/<reason>/*.yaml` must fail with that reason.
`scripts/verify/cluster/lib/vectors.sh contracts/stages contracts/stages/stages.yaml` runs them and also loads the pinned table.
`packages/stages/tests/test_stages_contract.py` runs the same loader test inside `just ci`.

## Changing it

A change after the pin is a `contract-change` PR, lists the consumer issues and bumps `Version`.
