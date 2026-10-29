# Preset contract

Owner epic: #2 (fixed-resource cluster and Tilt).
Version: 1
Consumers: every epic through `just up`, `just doctor` (reads `memory_mib`), the cluster renderer, the footprint spike and characterization.

A preset fixes the numbers of one cluster size, and the same repo runs at each size.
The three presets are `ci`, `dev` and `soak`, in `deploy/presets/<name>.yaml`.

## Shape

Schema: [preset.schema.json](preset.schema.json).

| Key | Meaning |
| --- | --- |
| `name` | preset name |
| `fitted` | `true` when the numbers are meant to run on a host that exists, `false` when the target host is still open (`soak`) |
| `memory_mib` | sum over `nodes` of `count * memory_mib`, checked by the loader, and read by `just doctor` with a `sed` match on `^memory_mib:` so it stays a top-level scalar |
| `cpus` | sum over `nodes` of `count * cpus`, checked by the loader |
| `nodes` | list of `{role, count, cpus, memory_mib}`, where the limits are per node |
| `host.ports` | path of the host ports table, shaped as below |
| `host.data_dir` | host directory for database and object storage data, `~/` or absolute |
| `mirror` | `true` when the package mirror runs in the preset |

`role` is one of `control-plane`, `infra`, `obs`, `compile`, `simulate`, `aggregate`.
Every preset has all six roles, and `aggregate` has its own node, so presets change only numbers.
`cpus` is a multiple of 0.5.
`just up` refuses a host below `memory_mib` whatever `fitted` says.

## Kubelet reservation

The reservation is derived from the node limit and is never stored in a preset.
For a node with `M` MiB:

- eviction-hard memory is `256 + ceil(M / 16)` MiB
- the node keeps at least 256 MiB allocatable after it, so a node is valid when `M - (256 + ceil(M / 16)) >= 256`

The renderer reads this rule from here, and the loader tests the boundary (547 MiB passes and 546 MiB fails).

## Host ports table

Schema: [ports.schema.json](ports.schema.json).
`deploy/host/ports.yaml` is a `ports` list of `{name, host_port, node_port}`.
Each row maps `127.0.0.1:<host_port>` to the NodePort `<node_port>`, so the listen address is never a key and cannot be widened.
`host_port` is 1024 to 65535, `node_port` is 30000 to 32767, and a `host_port` appears once.
The renderer places the mappings on the control plane node.

## Loader

`contracts/preset/check.sh <file>` validates a preset or a ports table.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.

| Reason | Fails when |
| --- | --- |
| `unknown-role` | a node role is not one of the six |
| `missing-role` | one of the six roles has no node |
| `schema-violation` | the file does not match the schema |
| `memory-sum-mismatch` | `memory_mib` is not the node sum |
| `cpus-sum-mismatch` | `cpus` is not the node sum |
| `node-memory-below-reservation` | a node leaves less than 256 MiB allocatable |
| `duplicate-host-port` | the ports table maps a host port twice |

## Vectors and the loader test

`vectors/valid/*.yaml` must pass, and `vectors/invalid/<reason>/*.yaml` must fail with that reason.
`scripts/verify/cluster/lib/vectors.sh contracts/preset deploy/presets/*.yaml` runs them and also loads the real presets.
The pytest wrapper `scripts/verify/cluster/lib/test_vectors.py` runs the same loader test inside `just ci`.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
