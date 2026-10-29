# Topology contract

Owner epic: #2 (fixed-resource cluster and Tilt).
Version: 1
Consumers: placement and workers, observability, load and chaos, and the cluster and namespace work inside the owner epic.

The topology fixes how nodes are labelled and tainted into pools and which namespaces exist.
The table is [topology.yaml](topology.yaml), validated by [topology.schema.json](topology.schema.json).

## Pools

Each pool node carries the label `wdf.dev/pool=<pool>` and the taint `wdf.dev/pool=<pool>:NoSchedule`.
Pool nodes are the preset roles `infra`, `obs`, `compile`, `simulate` and `aggregate`, and the pool name equals the role name.
The `control-plane` role has no pool and keeps its own taint.
The `wdf.dev` prefix is a placeholder, and consumers read the label key from `pool_label` in the table.
A label alone lets infra pods distort pool capacity, so the taint is part of the contract.

## Namespaces

Four namespaces exist: `wdf-infra`, `wdf-control`, `wdf-workers` and `wdf-obs`, one per module group.
Each carries a ResourceQuota and a LimitRange, because a quota without defaults rejects pods that lack requests and limits.
The `wdf-workers` quota is set slightly above pool allocatable, which is a guardrail against runaway creation while the scheduler's own accounting stays binding.
The quota numbers are derived from the preset, so they are not stored in the table.

## Rule for consumers

Detect an out-of-memory kill by exit code 137 plus node events, because `OOMKilled` is not reliably reported.
The `compile` and `aggregate` pools run one task per pod with requests equal to limits, so the pod limit is the per-task limit.

## Loader

`contracts/topology/check.sh <file>` validates a table.
It exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr when not, and 2 on usage.

| Reason | Fails when |
| --- | --- |
| `schema-violation` | the file does not match the schema, which includes a missing namespace or a taint effect other than `NoSchedule` |
| `preset-role-without-pool` | a preset role other than `control-plane` has no entry in `pools` |
| `pool-for-unknown-role` | `pools` has a key that is not a preset role |

The preset roles are read from `contracts/preset/preset.schema.json`, so the roles are listed once.

## Vectors and the loader test

`vectors/valid/*.yaml` must pass, and `vectors/invalid/<reason>/*.yaml` must fail with that reason.
`scripts/verify/cluster/lib/vectors.sh contracts/topology contracts/topology/topology.yaml` runs them and also loads the pinned table.
The pytest wrapper `scripts/verify/cluster/lib/test_vectors.py` runs the same loader test inside `just ci`.

## Changing it

A change after the pin is a `contract-change` PR that touches `contracts/` only, lists the consumer issues and bumps `Version`.
