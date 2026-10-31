# Status contract

Owner epic: #4 (job event envelope and state propagation).
Version: 1
Consumers: storage (#3), workflow (#6), admission (#8) and dispatch (#24).

The job status is a stored value on the job row, and every status is set by one job lifecycle event type.
The table is [status.yaml](status.yaml), validated by [status.schema.json](status.schema.json).
The guarded compare-and-set that applies a move to the row stays in the storage epic.

## Statuses

There are ten stored statuses: `accepted`, `admitted`, `running`, `paused`, `resume-pending`, `succeeded`, `partial`, `failed`, `cancelled` and `terminated`.
The terminal set is `succeeded`, `partial`, `failed`, `cancelled` and `terminated`, and nothing moves out of it.
`partial` is the terminal status of a run on partial inputs, and its event is `dev.wdf.job.partial.v1`, so a consumer that filters on `succeeded` never mistakes it for a complete run.
`accepted` is the stored name of the first status, and the older name `queued` is not used.

## Views

`pausing` and `cancelling` are views over a requested action that is still outstanding.
They are never stored, and the loader rejects a table that lists one as a status, projects an event to one or moves a row to one.
A reader derives a view from the stored status and the request, using `shown_while` in the table.

## Projection

`projection` maps each of the 16 job lifecycle event types to the status it sets on the row, or to `null` when it sets none.
The types are named without the `dev.wdf` prefix and the major, as the schema directories are.

- `job.accepted`, `job.admitted`, `job.started`, `job.paused`, `job.succeeded`, `job.partial`, `job.failed`, `job.cancelled` and `job.terminated` set the status of the same name, except that `job.started` sets `running`.
- `job.resume.requested` sets `resume-pending`.
- `job.resumed` sets `running` when the workflow emits it, and `accepted` when the control stage emits it for a job that never started.
- `job.published`, `job.pause.requested`, `job.cancel.requested`, `job.terminate.requested` and `job.dead.lettered` set no status.
- The row of a dead lettered job stays `accepted`, so that a replay can admit it.
  `job.dead.lettered` is emitted only for a message that parses far enough to give a job id, and the broker dead letter queue is the only record of a message that does not.

A type that sets different statuses for different emitters lists one entry per emitter with a `when` condition on `source`.

## Moves

`transitions` is the matrix of allowed moves of the row.
A job that never started is paused, cancelled or terminated on the row alone, so the control stage emits `job.paused`, `job.cancelled` and `job.terminated` for it, and `job.resumed` when such a paused job is resumed.
Those moves are marked `row_only`, and they include the pair `accepted` to `paused` and `resume-pending` to `accepted`.

## Loader

`contracts/status/check.sh <file>` validates a table.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match the schema, which includes a missing view |
| `view-stored-as-status` | `pausing` or `cancelling` is listed as a status, projected or the end of a move |
| `unknown-status` | a status is named that `statuses` does not list |
| `transition-out-of-terminal` | a terminal status has a move out of it |
| `ambiguous-projection` | an event type has more than one entry and an entry lacks a `when` condition |

## Vectors and the loader test

`vectors/valid/*.yaml` must pass, and `vectors/invalid/<reason>/*.yaml` must fail with that reason.
`scripts/verify/cluster/lib/vectors.sh contracts/status contracts/status/status.yaml` runs them and also loads the pinned table.
`packages/events/tests/test_contract_vectors.py` runs the same loader test inside `just ci`.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
