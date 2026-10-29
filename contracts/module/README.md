# Module contract

Owner epic: #2 (fixed-resource cluster and Tilt).
Version: 1
Consumers: the epics that ship an infrastructure component (state and storage, workflow engine, broker, observability) and the Tilt skeleton that loads them.

A module is how one component plugs into the cluster.
Each component ships `deploy/<component>/module.yaml` and `deploy/<component>/Tiltfile.module`.
The Tilt side loads one `Tiltfile.module` per component and registers one resource group named after the manifest `group`.

## Manifest

Schema: [module.schema.json](module.schema.json).

| Key | Meaning |
| --- | --- |
| `name` | module name, unique across `deploy/` |
| `group` | `infra`, `control`, `workers` or `obs` |
| `namespace` | the namespace of the group: `wdf-infra`, `wdf-control`, `wdf-workers` or `wdf-obs` |
| `artifact` | `{kind: chart or image, pin}`, where `pin` is a key under `charts` or `images` in `deploy/versions.yaml` |
| `resources` | one entry per workload, each with `requests` and `limits` of `cpu` and `memory` |
| `readiness` | `{command: [...]}` or `{resource: <kind>/<name>, condition: <type>}`, never both |
| `profiles` | any of `minimal`, `dev`, `full`, `ci`, the Tilt profiles that enable the module |
| `depends_on` | optional list of `<group>/<name>` entries the module waits for |

`cpu` is a count or millicores (`1`, `0.5`, `250m`) and `memory` is `Mi` or `Gi`.

## The obs rule

Nothing outside `obs` may depend on an `obs` resource, so the observability tier can be switched off without touching another group.
A manifest whose `group` is not `obs` and whose `depends_on` lists an `obs/...` entry is invalid.

## Loader

`contracts/module/check.sh <file>` validates one manifest.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.
Artifact pins resolve against `deploy/versions.yaml`, or against `WDF_VERSIONS` when set.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match the schema, which includes a namespace outside the group |
| `obs-dependency-from-other-group` | a non-`obs` module depends on an `obs` resource |
| `unpinned-artifact` | the pin is not under `charts` or `images` in the versions file |

## Vectors and the loader test

`vectors/valid/*.yaml` must pass, and `vectors/invalid/<reason>/*.yaml` must fail with that reason.
`vectors/versions.yaml` stands in for the versions file for the vectors only.
`scripts/verify/cluster/lib/vectors.sh contracts/module deploy/*/module.yaml` runs them and also loads every real manifest, so a real module that breaks the shape fails the same vectors.
The pytest wrapper `scripts/verify/cluster/lib/test_vectors.py` runs the same loader test inside `just ci`.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
