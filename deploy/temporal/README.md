# Temporal

The Temporal server of the platform, from the official chart `temporal`, with persistence and SQL visibility on [`temporaldb`](../temporaldb/Tiltfile.module).
Module: `temporal`.
The sizing and the pass of the chart on the `ci` preset are in [the S10 spike](../../docs/delivery/spikes/S10/helm-install.md).

## Files

| File | Holds |
| --- | --- |
| `temporal-<version>.tgz` | the chart, vendored so a render needs no network, and checked against the digest of `charts.temporal` in `deploy/versions.yaml` at every load |
| `values.json` | the chart values: the two stores, 512 shards, one replica per service, the dynamic config, and no web UI, admin tools pod or namespace job |
| `module.yaml` | the module manifest, whose `resources` are the limits of each service and of the schema job |
| `Tiltfile.module` | renders the chart with `values.json` and an overlay of the image pins, the infra pool placement, the limits of `module.yaml` and the checksum of the dynamic config |
| `test_render.py` | the render tests, offline through `tilt alpha tiltfile-result` |

## Tilt resources

Tilt cannot put four Deployments into one resource, so each service has its own, and the resource `temporal` is the frontend.

| Resource | Holds | Waits for |
| --- | --- | --- |
| `temporal-schema` | the schema Job and the two ConfigMaps | `temporaldb` |
| `temporal-history`, `temporal-matching`, `temporal-worker` | one Deployment each | `temporal-schema` |
| `temporal` | the frontend Deployment | the three above |

Profiles enable `temporal` by name, and Tilt enables the resources it depends on, so `temporal` is ready only when the schema is in place and every service is ready.

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
