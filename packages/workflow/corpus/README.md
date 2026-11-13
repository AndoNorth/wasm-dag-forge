# JobWorkflow replay corpus

Recorded histories of `JobWorkflow`, one per behaviour, which every change to the workflow must still replay.
The file format, the runner and the retirement of a release are those of the [`wdf_temporal` replay corpus](../../temporal/README.md#replay-corpus).
`owners.yaml` maps the workflow type to the epic that owns these histories.

## The gate

`tests/replay/test_replay.py` replays every history here with `wdf_temporal.corpus.replay_corpus([JobWorkflow], root)`.
It needs no container, so it runs in `just test` and in CI.
It also checks from the committed files that each history holds the behaviour it was recorded for, and `test_replay_detects_reordered_activity` shows that a changed workflow fails the replay while a build that only delegates passes it.

```sh
nix develop -c uv run --frozen pytest packages/temporal packages/workflow -v -k replay
```

| History | Behaviour |
| --- | --- |
| `v1/plain.json` | a compile, two simulate tasks and an aggregate, each succeeding at its first attempt |
| `v1/retry.json` | a simulate task that times out once and succeeds at its second attempt from the guest budget |
| `v1/cache-hit.json` | a second job whose compile is skipped on the cache entry the first job recorded |
| `v1/partial-aggregate.json` | an `allow_partial` aggregate over one failed input, ending the job `partial` |
| `v1/cancel.json` | a `cancel` signal while a task runs, ending the job `cancelled` |
| `v1/pause-resume.json` | a checkpointed task that exhausts its budget, a pause, and a `resume` that runs it to success |

## Recording a release

```sh
nix develop -c uv run --frozen python packages/workflow/tests/replay/record.py <release>
```

The recorder runs each behaviour on one dev server through `wdf_itest`, with `StaticPlacer`, `FakeControl` and a `FakePool` per pool.
Its workers poll the queues the service polls, the workflow worker under worker versioning, and its client carries the tracing interceptor as the service's does.
The build label of `wdf_temporal.corpus` names the recording build, and a fixed identity and empty failure stack traces keep host names, process ids and local paths out of the files.
It refuses a release directory that exists, since the corpus only grows.

## Rules for changing the workflow

The workflow is `wdf_workflow.job` and every module it runs inside a workflow, such as `taskpath`, `ready`, `result`, `budgets` and `aggregate`.

1. A change to the workflow passes the gate as it is, or it is guarded.
2. A change to the commands a recorded history expects goes behind `workflow.patched("<change>")` and keeps the old branch for histories without the marker.
   Such a change adds, removes, reorders or renames an activity, a local activity or a timer, or changes when the workflow completes.
3. Replay compares the commands of the workflow with the history, and not their arguments, task queues or the job's result, so a change of those alone passes the gate and is reviewed for what a running job would see.
4. A failing replay is never fixed by editing, re-recording or deleting a history.
5. A history is never edited by hand, and a release is never recorded again.
   A behaviour that the corpus lacks is recorded as a new release beside the old ones, and its histories are added to `RECORDED` in the test.
6. A `patched` branch is removed, through `workflow.deprecate_patch` first, only once the release whose histories need it is retired.

Every job is pinned to the build that started it, so a new build meets an open job only when `wdf_workflow.versioning.move` puts it on a fixed build.
That build replays the whole history, and the move reverts when it cannot, so these rules are what keeps a fix able to land on a running job.
