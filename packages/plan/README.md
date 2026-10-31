# wdf_plan

Compiles a validated job document to a plan and gives the plan its identity.
The contract is [contracts/job/](../../contracts/job/README.md), and the rationale is in [docs/epics/04-validator.md](../../docs/epics/04-validator.md).

## Compiling

`compile_plan(document, profile)` is a function of the document and the limits profile only.
Per-job values never enter it, so a plan keeps its placeholders and is the template that a whole batch shares.

- Tasks are in topological order, and among the tasks that are ready the smallest task id comes first.
- `depends_on` of a plan task is the sorted union of the document's `depends_on` and the producer of every input, so a data edge is also a control edge.
- `inputs` are sorted by task then output.
- A compile task takes its cpu, memory, timeout and retries from the profile: the compile slot of the pool table, `compile_run_timeout_s` and `compile_retries`.
  The document never sets them, and the `permissive` profile lifts only the check ceilings, so its compile defaults are the same.
- `max_parallel` is the document's value, or the profile default.
- Optional fields that are absent are omitted from the plan, so a spelled-out empty `args` and a missing one give the same bytes.

A graph with a cycle or an unknown dependency is refused with `ValueError`, because the validator rejects both before a plan is ever compiled.

## Identity

`canonical_bytes(plan)` is the plan as JSON with sorted keys, no whitespace and ASCII escapes, and a plan holds no floats, so no number formatting enters.
`plan_hash` is the SHA-256 of those bytes.
Applied to the template plan it is the manifest hash, and `manifest_reference(plan)` is the content-addressed reference of kind `manifest` to the bytes, which `contracts/artifact/check.sh` accepts.

## Task requests

`task_request_fields(task)` gives the `task_type`, `cpu_cores` (millicores over 1,000) and `memory_mib` of a plan task.
The workflow adds the deadline and the input references, and a test builds a `TaskRequestData` from every task so the two cannot drift.
