# wdf-stages

The fake stages and the contract runner of the stage contract, imported as `wdf_stages`.
The contract itself is [contracts/stages/README.md](../../contracts/stages/README.md), and the table it validates is `contracts/stages/stages.yaml`.

## A stage

A stage is a callable `(inbound, facts) -> list[Event]`.
`inbound` is a `Recorded` event, or `None` for a stage that is started by a request and not by an event.
`facts` carries what the stage knows besides the message it reads: the job identity, the clock, the resume counter and the sequence.
`Recorded` records every context attribute and every key of `data` that the stage reads, and `name` is the type the message was delivered as, which is routing and not a read.

## The fakes

`FAKES` holds one fake per stage: `dispatch`, `control`, `admission`, `workflow`, `placement` and `worker`.
A fake reads only what its stage declares, and writes the event types named in `facts.emit` under its own source.
The workflow builds the task request with `build_task_request` and never returns it as an emitted event.
The placement fake picks a queue and emits nothing, and the worker fake writes references into `facts.heartbeat`.

## `run_stage_contract`

`run_stage_contract(stage_name, stage_callable)` runs any callable over a sample of each type its stage declares to read and to emit.
It raises `ContractViolation` when the callable

- reads an attribute that the table does not declare for the stage
- reads a data field that the table does not declare for the type it received
- emits a type, or a type under a source, that the table does not declare
- emits an event that does not survive `to_bytes` and `from_bytes`

A stage epic adds one test that calls `run_stage_contract` with its real stage, wrapped to the signature above, so that a fake cannot drift from the real stage silently.

```python
def test_admission_keeps_its_contract():
    run_stage_contract("admission", real_admission_adapter)
```

Pass `cases` to run the callable over inputs of the epic's own.
`invoke(stage_name, stage_callable, inbound, facts)` runs one call with the same checks and returns the emitted events, which is how `tests/test_flow.py` drives a whole job.
