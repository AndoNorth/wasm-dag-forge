# Workflow worker

The workflow worker service, `python -m wdf_workflow.service`, deployed in `wdf-control` on the infra pool.
One process runs the workflow worker of one build, its control activity worker on `wdf-control-<build id>` and the reaper's Schedule worker.
Module: `workflow-worker`.
Diagram: [workflow-worker.drawio.svg](workflow-worker.drawio.svg), the module for one build id, its Secrets, the logins and a second build during a rollout.

## Files

| File | Holds |
| --- | --- |
| `module.yaml` | the module manifest: the pod at 256 MiB of memory, requests equal to limits, and the nominal entry of the login resource |
| `Tiltfile.module` | the image build, the two Secrets, the login resource and the Deployment of the build id |
| `image.sh`, `Dockerfile` | the image: wheels of the workspace packages built on the host, over the third-party packages that `uv.lock` pins by hash, on the pinned `python` image of `deploy/versions.yaml` |
| `logins.sh`, `logins.py` | the service logins in the app database, and the render of the Secrets |
| `test_render.py`, `test_logins.py` | the render tests, offline through `tilt alpha tiltfile-result`, and the integration tests of the logins against a migrated database |

## Build id

The build id is `WDF_BUILD_ID` when it is set, and the short commit otherwise.
An export with no commit needs `WDF_BUILD_ID`, and Tilt refuses to load without one.
The image carries it as `WDF_BUILD_ID`, and it names the Deployment `workflow-worker-<build id>`, the control queue and the worker deployment version.
It is a lower-case name of at most 40 letters, digits and inner dashes.

Each build runs as its own Deployment, so a second build runs beside the first during a rollout and the first is deleted only once it is drained.
Within one build the Deployment uses the `Recreate` strategy, because a surge pod does not fit the infra node.
Making a build current in the worker deployment is the job of `wdf_workflow.versioning`, not of this module.

To build the image by hand:

```sh
WDF_BUILD_ID=<build id> deploy/workflow/image.sh <ref>
```

## Logins and Secrets

The group roles `wdf_workflow`, `wdf_artifacts` and `wdf_reaper` cannot log in, so the service has one login in each.

| Login | Group role | Used by | DSN variable |
| --- | --- | --- | --- |
| `workflow_worker_workflow` | `wdf_workflow` | `emit` | `WDF_WORKFLOW_DSN` |
| `workflow_worker_artifacts` | `wdf_artifacts` | `commit`, `index_checkpoint`, the cache activities and `build_index` | `WDF_ARTIFACTS_DSN` |
| `workflow_worker_reaper` | `wdf_reaper` | the reaper pass | `WDF_REAPER_DSN` |

Their passwords are generated on first use into `workflow-worker.logins.json` in the state directory of the cluster scripts, readable by its owner only, and never committed.
The Secret `workflow-worker-logins` holds the three DSNs, and the Secret `workflow-worker-store` holds `credentials.json` with the `workflow`, `indexer` and `reaper` store identities only, taken from `objectstore.credentials.json`.

Tilt runs `logins.sh apply` as the resource `workflow-worker-logins` once the migrations are applied, and the service starts after it.
It creates a missing login, and converges one whose password, login right or group membership differs, leaving it a member of its group role only.
A second run prints every login `unchanged` and `changed: 0`.
`logins.sh check` exits 1 when a login differs or cannot log in with its password.

## Readiness

The service creates `/tmp/wdf-ready` once Temporal answers for namespace `wdf` and its workers poll, and removes it when it starts to drain.
The readiness probe tests for the file, and the module is ready when the Deployment of the build is `Available`.
The log shows the three queues and the build id:

```sh
kubectl --context kind-wdf -n wdf-control logs deploy/workflow-worker-<build id>
```

## Changing the base image

Set `version` and `digest` of `images.python` in `deploy/versions.yaml`, keeping the Python minor of `requires-python`, then run `pytest deploy/workflow -v` and `scripts/tilt/run.sh ci` on a cluster.
