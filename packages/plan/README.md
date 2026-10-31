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

## Stats and the class hint

`stats(plan, profile)` gives what admission and placement read from the job row instead of parsing the plan.

- `max_width` is the widest topological level, a plain bound that is not the exact antichain.
- `critical_path_timeout_bound_s` is the longest path of `run_timeout_s` times one plus `retries`.
  It is a bound along one path and not a wall-clock bound, because a job wider than `max_parallel` also waits, so the class hint has the same limit.
- `chain_count` is the number of compile tasks, and `chains(plan)` lists the simulate tasks that name each compile task's `component` output in their `inputs`.
  `depends_on` alone never puts a task in a chain.
- `peak_cpu_milli` and `peak_memory_mib` are the demand of the busiest level when its `max_parallel` largest tasks run at once, and the `largest_*` figures are the biggest single task.
- `estimated_history_events` is the profile's per-job figure plus, per task, its per-task figure and its per-retry figure times the task's retries.

`class_hint(stats, profile)` returns `short` when the critical path bound is at most the profile threshold and `long` above it.
The submitter's override on the job row wins over the hint, and that choice is made by the caller.
