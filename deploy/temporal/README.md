# Temporal

The Temporal server of the platform, from the official chart `temporal`, with persistence and SQL visibility on [`temporaldb`](../temporaldb/Tiltfile.module).
Module: `temporal`.
The sizing and the pass of the chart on the `ci` preset are in [the S10 spike](../../docs/delivery/spikes/S10/helm-install.md).
Diagram: [temporal-module.drawio.svg](temporal-module.drawio.svg), the render inputs, the Tilt resources with their limits, and a key change that rolls the pods.

## Files

| File | Holds |
| --- | --- |
| `temporal-<version>.tgz` | the chart, vendored so a render needs no network, and checked against the digest of `charts.temporal` in `deploy/versions.yaml` at every load |
| `values.json` | the chart values: the two stores, 512 shards, one replica per service, the dynamic config, and no web UI, admin tools pod or namespace job |
| `module.yaml` | the module manifest, whose `resources` are the limits of each service and of the schema job |
| `Tiltfile.module` | renders the chart with `values.json` and an overlay of the image pins, the infra pool placement, the limits of `module.yaml` and the checksum of the dynamic config |
| `test_render.py` | the render tests, offline through `tilt alpha tiltfile-result` |
| `bootstrap/` | module `temporal-bootstrap`, below |

## Tilt resources

Tilt cannot put four Deployments into one resource, so each service has its own, and the resource `temporal` is the frontend.

| Resource | Holds | Waits for |
| --- | --- | --- |
| `temporal-schema` | the schema Job and the two ConfigMaps | `temporaldb` |
| `temporal-history`, `temporal-matching`, `temporal-worker` | one Deployment each | `temporal-schema` |
| `temporal` | the frontend Deployment | the three above |

Profiles enable `temporal` by name, and Tilt enables the resources it depends on, so `temporal` is ready only when the schema is in place and every service is ready.

## Bootstrap

Module `temporal-bootstrap` runs on the host as a Tilt `local_resource` once `temporal` is ready, through a port-forward to the frontend bound to 127.0.0.1.
It creates namespace `wdf` with a retention of 24h, registers the search attributes of [`naming.yaml`](../../contracts/temporal/naming.yaml) in it, and creates the reaper's Schedule `wdf-artifact-reaper` there with `wdf_artifactreaper.schedule.ensure_schedule`.
Diagram: [bootstrap/bootstrap.drawio.svg](bootstrap/bootstrap.drawio.svg), the items it creates and what a second run changes.

| File | Holds |
| --- | --- |
| `bootstrap/bootstrap.sh` | waits for the frontend, opens the port-forward and runs `bootstrap.py` |
| `bootstrap/bootstrap.py` | `apply` creates or converges each item and prints one line per item with what it did, ending with `changed: <n>`, and `check` exits 1 when the server differs |
| `bootstrap/test_bootstrap.py` | the integration tests against a dev server: two applies, the second changing nothing, and drift found and repaired |

A run that finds everything in place prints every item `unchanged` and `changed: 0`.
A drifted retention or Schedule is converged, a paused Schedule stays paused, and a search attribute of another type is reported, because Temporal cannot change its type in place.
Run it by hand with `deploy/temporal/bootstrap/bootstrap.sh [apply|check]`.

## Changing a dynamic config key

Edit `server.dynamicConfig` in `values.json`.
The cache keys are read only at start, so the pod templates carry `checksum/dynamic-config`, and a changed key rolls every Temporal pod.
The Deployments use the `Recreate` strategy, because a surge pod does not fit the quota of `wdf-infra`.

## Bumping the chart or the server

1. Download the new chart with `helm pull temporal --repo https://go.temporal.io/helm-charts --version <version>` and replace the tarball here.
2. Set `version` and `digest` of `charts.temporal`, `images.temporalio/server` and `images.temporalio/admin-tools` in `deploy/versions.yaml`, the chart digest being the `sha256sum` of the tarball.
3. Run `pytest deploy/temporal -v`, then `scripts/tilt/run.sh ci` on a cluster.

## Reaching Temporal from the host

```sh
kubectl --context kind-wdf -n wdf-infra port-forward --address 127.0.0.1 svc/temporal-frontend 17233:7233
```
