# wdf-workflow

The job workflow on Temporal, its control activities and worker service, and the models and the loader of the activity and Temporal contracts.
The contracts it loads are pinned in `contracts/activity/README.md` and `contracts/temporal/README.md`.

## State

The models (`wdf_workflow.models`), the loader `python -m wdf_workflow.contract --check FILE [--as NAME]` and the schema generator `python -m wdf_workflow.gen [--check]` are delivered.
The placer, the fakes and their conformance tests, the pure rules, the control activities, the workflow, the service and the harness are not built yet.
`pyproject.toml` declares every dependency those parts need, so no later change edits it.

## Package data

`wdf_workflow/contract/` holds links to the contract files the package reads: the pinned failure class table, naming and pool table, and the exit classes, artifact failures, staging, registry and status files of the other contracts.
A built wheel carries the files themselves, so the package never reads a repository path.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/workflow -q
nix develop -c uv run --frozen python -m wdf_workflow.contract --check FILE
nix develop -c uv run --frozen python -m wdf_workflow.gen --check
```
