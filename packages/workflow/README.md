# wdf-workflow

The job workflow on Temporal, its control activities and worker service, and the models and the loader of the activity and Temporal contracts.
The contracts it loads are pinned in `contracts/activity/README.md` and `contracts/temporal/README.md`.

## State

The models (`wdf_workflow.models`), the loader `python -m wdf_workflow.contract --check FILE [--as NAME]` and the schema generator `python -m wdf_workflow.gen [--check]` are delivered.
The placer and the stubs are delivered too:

- `wdf_workflow.placement`: the `Placer` interface, `StaticPlacer` over pool table v0 and the `place` local activity.
- `wdf_workflow.testing`: `placer_contract`, `FakePool` with `activity_contract`, and `FakeControl` with `control_activity_contract`, where `wdf_workflow.testing.control` pins the argument and result of each control activity.

The rest of the package is delivered as well:

- The pure rules: `ready`, `result`, `failures` and `budgets`.
- `wdf_workflow.activities`: the control activities.
- `wdf_workflow.job`: `JobWorkflow`, with `wdf_workflow.versioning` for its builds and the replay corpus in `corpus/`, whose rules are in `corpus/README.md`.
- `wdf_workflow.service`: the worker service, which `deploy/workflow/README.md` deploys.
- `wdf_workflow.harness`: the harness that runs smoke, restart and rollout jobs against a dev server or the cluster.

## Package data

`wdf_workflow/contract/` holds links to the contract files the package reads: the pinned failure class table, naming and pool table, and the exit classes, artifact failures, staging, registry and status files of the other contracts.
A built wheel carries the files themselves, so the package never reads a repository path.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/workflow -q
nix develop -c uv run --frozen pytest packages/workflow -v -k "placer_contract or activity_contract or control_contract"
nix develop -c uv run --frozen python -m wdf_workflow.contract --check FILE
nix develop -c uv run --frozen python -m wdf_workflow.gen --check
nix develop -c uv run --frozen python -m wdf_workflow.service
nix develop -c uv run --frozen python -m wdf_workflow.harness (--address HOST:PORT | --kube-context NAME) describe|smoke|restart|rollout
```

`wdf_workflow.service` reads its configuration from the environment only and exits 2 when a variable is missing.
The harness exits 0 when every job finished, 1 when one did not, and 2 for a usage error or a command the tier refuses.
