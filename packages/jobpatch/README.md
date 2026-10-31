# wdf_jobpatch

Decides whether a changed plan may replace the plan of a paused job.
The contract is [contracts/job/](../../contracts/job/README.md), and the rationale is in [docs/epics/04-validator.md](../../docs/epics/04-validator.md).

`patch_check(old, new, completed)` takes two bound plans and the set of completed task ids, and returns a `PatchReport` with `compatible`, `issues`, `changed` and `recompile`.
It is pure: neither plan nor the set is modified, and equal inputs give equal reports.

- The frozen fields are everything outside the changeable parameters, so a field added to the plan later is frozen until the rule names it.
  They include the plan version, profile, `max_parallel`, parameter declarations, the task id set, and per task the type, pool, edges, resources, timeout, retries, `checkpoint` and `allow_partial`.
- Resources, timeout and retries of a compile task are in the plan like any other task's, so two plans compiled under different slots files differ.
- The changeable parameters of work that has not completed are the `source` and `entry` of a compile task, the `workload`, `args` and `seed` of a simulate task, and the `aggregation` and `args` of an aggregate task.
- A completed simulate or aggregate task may not differ at all.
- A changed compile task is listed in `recompile`, unless a completed simulate task is in its chain, which is `build-changed-after-result`.
  The chain is the simulate tasks that name the compile task's `component` output in their `inputs`, and `depends_on` alone never puts a task in it.
- A changed simulate or aggregate task that has not completed is listed in `changed`.
  A checkpointing simulate task whose seed changes is `seed-changed-on-checkpoint-task` instead, because a checkpoint written under the old seed would be resumed under another.
- A completed id that is not a task of the old plan is `unknown-completed-task`.

Issue paths are JSON Pointers into the plan with the task id in place of its index, such as `/tasks/sim/params/args`.
Whether a paused task with a committed checkpoint can resume under changed code is not decided here, and `changed` lists every such task for the checkpoint contract to apply.

## Vectors

`contracts/job/vectors/patch/<verdict>/<case>/` holds `old.json` and `new.json` (documents), `params.json` and an optional `new-params.json` (the job values each side is bound with), `completed.json` and `expected.json` with the issue `codes`, `recompile` and `changed`.
The tests compile every case under the `ci` profile and the slots fixture, so an edit of the live slots files never changes a verdict.
