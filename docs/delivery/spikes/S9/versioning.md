# Spike: pinned versioning on SDK 1.35.0 and the pinned-bug override (S9)

Run on 2026-11-10.
Owner: the [workflow engine epic](https://github.com/AndoNorth/wasm-dag-forge/issues/6), lane S, filed as [spike issue 315](https://github.com/AndoNorth/wasm-dag-forge/issues/315).
Spec: the S9 block of [the workflow engine breakdown](../../epics/08-workflow-engine.md#s9-versioning-on-sdk-1350-and-the-pinned-bug-override), and decision D6 there.
Method: [docs/delivery/method.md](../../method.md).
Throwaway code: [code/](code/), run as `nix develop -c uv run python docs/delivery/spikes/S9/code/spike.py [out-dir]`.
Every number and finding below is a `figure <name> <value>` line of that run, and the name is given in backticks beside it.

## Result

Diagram: [versioning.drawio.svg](versioning.drawio.svg), a worked example of the rollout from A to B with the override and the drain.

**Pass.**
All five items of the pass line hold on the dev server of `temporalio/temporal` 1.9.1, which reports server 1.32.0 (`server.version`, `server.cli`), with `temporalio` 1.35.0 (`sdk.temporalio`).
The tables below are from the second of two full runs, and the first gave the same value for every figure both recorded.

- (1) A pinned workflow started on A finished on A while B was current, and `workflow.info().get_current_deployment_version()` reported A at all four of its steps (`stays.result`, `stays.pass`).
- (2) The raw `set_worker_deployment_current_version` call, with the `conflict_token` of `describe_worker_deployment` and the client identity, made A current on a fresh deployment and then B (`current_a.pass`, `current_b.pass`).
- (3) The raw `update_workflow_execution_options` call with a `PinnedVersioningOverride` to B moved an open pinned workflow from A to B, and it completed on B's code through the `workflow.patched` branch (`moved.result`, `moved.pass`).
- (4) One unversioned activity worker on the pool queue served 4 activity calls from A and 5 from B (`pool.served`, `pool.pass`).
- (5) The count of running pinned workflows on A was 0 once the last one closed, and `describe_worker_deployment_version` reported `VERSION_DRAINAGE_STATUS_DRAINED` 176 s later at the server defaults (`count.pinned_a.after_close`, `drain.a.seconds_after_close`).

The fail line was not reached, so the fallback "pause, terminate and resubmit the job, keeping its artifacts" is not needed for V.2.

## Question

Does pinned versioning hold on `temporalio` 1.35.0 against the pinned dev server, do the raw deployment calls set the current version and move an open pinned workflow to a fixed build, and does a visibility count detect that a build is drained?
SDK 1.35.0 has no client method for worker deployments, so D6 relies on the raw gRPC calls, and the SDK source leaves four points open, answered in "The four open points" below.

## Environment

| Item | Value |
| --- | --- |
| Host | WSL2, 20 GiB VM, 16,406 MiB `MemAvailable` before the run (`host.mem_available_mib.before`) |
| Server | `temporalio/temporal:1.9.1` pinned by digest in `deploy/versions.yaml`, `temporal server start-dev --headless`, which reports `temporal version 1.9.1 (Server 1.32.0, UI 2.54.1)` (`server.cli`) and server version 1.32.0 from `GetSystemInfo` (`server.version`) |
| SDK | `temporalio` 1.35.0 from `uv.lock` (`sdk.temporalio`), `uv.lock` unchanged |
| Containers | one, the dev server, started through `wdf_itest.Session` with its T1 lock file, 1 GiB cap, port on 127.0.0.1 only and the run label, removed after the run |
| Left behind | none of this run: `docker ps -a --filter label=wdf.dev/verify-run` listed 0 at the end of the run (`containers.left.label_all`) |
| Workers | all in the spike process: builds A, B and C of [code/builds.py](code/builds.py) as versioned workers of deployment `wdf-workflow-worker` on queue `wdf-workflow`, and one unversioned worker on `wdf-pool-simulate` |

## The builds

One workflow type, `JobWorkflow`, declared `@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)` in three builds, each on a worker with `WorkerDeploymentConfig(version=WorkerDeploymentVersion("wdf-workflow-worker", <build>), use_worker_versioning=True)`.
The workflow runs a `prepare` activity on the pool queue, waits on the signal `go`, runs a finish activity, and returns its build, its steps and the version the SDK reported at each of four points.

- A is the build with the bug: its finish step is `finish-buggy`.
- B fixes the finish step behind `workflow.patched("fix-finish")`, so a history from A replays on B and the code after the signal takes the fixed branch: `finish-fixed`.
- C makes an unguarded change before the signal: a 0.1 s timer before `prepare`, so a history from A or B does not replay on C.

## Measured numbers

### Routing

| Step | Current before | Current after | Previous version in the response | Conflict token changed | Last modifier | Names |
| --- | --- | --- | --- | --- | --- | --- |
| Set A on the fresh deployment | none | A | none | yes | `s9-spike` | `current_a.*` |
| Set B after B's pollers are seen | A | B | A | yes | `s9-spike` | `current_b.*` |

Before any version was current, a workflow started on `wdf-workflow` completed 0 workflow tasks in 3 s, although A's pollers were registered (`no_current.completed_tasks`).
After A became current, the same workflow completed 2 tasks within 3 s (`no_current.completed_tasks_after_a`), so a task queued with no current version is not lost and goes to the build made current.

### Workflows

| Workflow | Started on | What happened | Versions reported at the four points | Steps | Names |
| --- | --- | --- | --- | --- | --- |
| `stays-on-a` | A | signalled after B became current | A, A, A, A | `prepare`, `finish-buggy` | `stays.*` |
| `moved-to-b` | A | override to B, then signalled | A, A, B, B | `prepare`, `finish-fixed`, 1 patch marker | `moved.*` |
| `started-on-b` | B | signalled | B, B, B, B | `prepare`, `finish-fixed` | `on_b.*` |
| `moved-to-c` | B | override to C, signalled, override back to B | B, B, B, B | `prepare`, `finish-fixed` | `unguarded.*` |

The SDK reports the version in its canonical form with a dot, `wdf-workflow-worker.A`, while the search attribute holds `wdf-workflow-worker:A` with a colon.
The moved workflow reports A for the points B replays and B for the points B runs, so the value is deterministic across the move, as the SDK documents.

### Visibility count

The query is `TemporalWorkerDeploymentVersion = 'wdf-workflow-worker:A' AND TemporalWorkflowVersioningBehavior = 'Pinned' AND ExecutionStatus = 'Running'`.

| Moment | Count on A | Count on B | Names |
| --- | --- | --- | --- |
| Two workflows on A waiting, before B | 2 | | `count.pinned_a.before_b` |
| After the override of one to B | 1 | | `count.pinned_a.after_override` |
| After the last workflow on A closed | 0 | 0 | `count.pinned_a.after_close`, `count.pinned_b.after_close` |
| When the server reported A drained | 0 | | `count.pinned_a.at_drained` |

The drainage status of A turned `VERSION_DRAINAGE_STATUS_DRAINED`, with the version status `WORKER_DEPLOYMENT_VERSION_STATUS_DRAINED`, 176 s after the last workflow on A closed, polled every 5 s, in both runs (`drain.a.seconds_after_close`, `drain.a.version_status`).
The count gave the same answer at once.

## The four open points

1. **Update mask path.** `versioning_override`, the snake case field name, is accepted (`override.mask.snake`).
   The camel case `versioningOverride` is refused with `INVALID_ARGUMENT: error parsing UpdateMask: proto: invalid path "versioningOverride" for message "temporal.api.workflow.v1.WorkflowExecutionOptions"` (`override.mask.camel`).
   An empty mask is accepted and changes nothing: the call returns OK and the workflow has no override afterwards (`override.mask.empty`, `override.mask.empty_describe`), so a missing path fails silently and V.2 checks the override with a describe.
2. **Does an override schedule a workflow task on its own?** No.
   The only event the override adds is `WORKFLOW_EXECUTION_OPTIONS_UPDATED`, and 5 s later no workflow task was scheduled (`override.new_events`).
   The moved workflow ran on B only when the signal gave it a task, so a workflow waiting on a timer or a signal stays where it is until that event.
3. **Do the search attributes follow a moved workflow before its next task completes?** Yes.
   Right after the override and before any task, the moved workflow counts 0 under A and 1 under B, and the same 5 s later (`override.visibility.a_now`, `override.visibility.b_now`, `override.visibility.a_5s`, `override.visibility.b_5s`).
   Describe shows the split at that moment: the execution's `deployment_version` is still A and its `versioning_override` is B (`override.describe_now`).
   So the drain count of A drops when the override lands, not when the moved workflow next runs.
4. **An override to a build with an unguarded change.** The next workflow task on C fails with `WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR`, message `[TMPRL1100] Nondeterminism error: Timer machine does not handle this event: HistoryEvent(id: 5, ActivityTaskScheduled)`, and the workflow stays `RUNNING` with 1 failed task in its history after 10 s (`unguarded.cause`, `unguarded.message`, `unguarded.status`, `unguarded.task_failed_events`).
   Nothing is lost: an override back to B is accepted and the workflow then completes on B (`unguarded.back_to_b`, `unguarded.recovered_result`).

## What the numbers change

1. D6 holds as written: `JobWorkflow` pinned, the workflow worker versioned, the control, reaper and pool workers unversioned, the current version set with the raw call at every deploy including the first, and the override with the raw call.
2. A deployment with no current version holds new workflows back rather than failing them, which confirms D6's rule that the first deploy also sets the current version.
3. V.2 sends the override with the update mask path `versioning_override` and reads the workflow back with describe, because an empty or wrong mask is either refused or silently ignored.
4. V.2 counts drainage with the query of (5), which drops when the override lands, and reports the server's status beside it, which took 176 s at the defaults here.
5. A moved workflow that waits on a signal or timer moves only at its next task, so V.2 reports "overridden" when the call lands and "on the fixed build" only after that task.
6. An override to a build that does not replay the history leaves the workflow running and stuck on a failing task, and overriding it back recovers it, so V.2's override procedure is: override, wait for the next task, and on `NON_DETERMINISTIC_ERROR` override back and report.
   The determinism gate of V.1 is what keeps such a build from shipping.
7. Code that compares the SDK's version with a search attribute converts between the dot of `to_canonical_string()` and the colon of the search attribute.

## Caveats and what was not measured

- One host, two runs of the same sequence on a fresh dev server each.
  The first run's check of (1) compared against the colon form and so printed `stays.pass False` on the right versions, and the second run compares the dot form.
- The dev server uses its in-memory SQLite store and visibility, not the Postgres persistence and SQL visibility of the cluster, so visibility timing and the drainage refresh may differ there.
  The drainage time is at the server defaults and was not tuned with dynamic config.
- All workers ran in one process with `UnsandboxedWorkflowRunner`, so three builds of one workflow type could share a module, and the workflow sandbox was not exercised.
- Ramping, `allow_no_pollers`, `ignore_missing_task_queues`, a conflict token that is stale, the deletion of a drained version, and a workflow paused before the override were not measured.
- How long the server keeps retrying a task that fails with a nondeterminism error, beyond the 10 s observed, was not measured.
