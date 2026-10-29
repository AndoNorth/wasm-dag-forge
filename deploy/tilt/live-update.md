# Live update for Python services

Live update syncs edited source into a running container, so an edit shows up in seconds without an image rebuild.
It exists for iteration, and only the `dev` profile turns it on.

## Convention

A Python service opts in from its `Tiltfile.module` and calls the library function instead of `docker_build`:

```python
load('../../tilt/live_update.star', 'python_live_update')

python_live_update('wdf-<service>', '.', src='src', dest='/app')
```

The `load` path is relative to the module file, so it depends on the module's depth under `deploy/`.

| Rule | Detail |
| --- | --- |
| Layout | the service keeps its source under `src`, its dependencies in `requirements.txt` and its `Dockerfile` in the module directory |
| Sync | `src` syncs to `<dest>/src` and `requirements.txt` to `<dest>/requirements.txt` |
| Dependencies | a change to `requirements.txt` runs `pip install -r` inside the container |
| Reload | the service process reloads itself, for example `uvicorn --reload`, because the function never restarts the container |
| Opt-in | a module that does not call the function is built as a plain image in every profile |

## Modes

`deploy/tilt/profiles.yaml` carries a `live_update` flag per profile, and the Tiltfile exports it as `WDF_LIVE_UPDATE` before any module loads.

| Profile | Live update |
| --- | --- |
| `dev` | on |
| `minimal`, `full`, `ci` | off |
| `soak` | off, with the module selection of `ci` |

`scripts/tilt/run.sh soak` runs `tilt ci` on the `soak` profile.
Every image in that run is built without live-update steps, so the deployed tag is the content-addressed tilt tag and the running code matches the built image.
`scripts/tilt/test_tiltfile.py` asserts offline that no resource in the `soak` profile has a live-update step.

## Why live update is only for iteration

Temporal replays the history of an open workflow against the worker code to rebuild its state.
Live-reloaded code can change a workflow function while workflows are still open, and a replay against the changed code can diverge from the recorded history.
That divergence is a non-determinism error, and it can stall the workflow.
A soak holds workflows open for a long time, so it runs immutable images, and a code change there means a new image tag and a deliberate rollout.
