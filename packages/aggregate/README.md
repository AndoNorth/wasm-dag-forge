# wdf-aggregate

The aggregate task: the `results.xml` files that an aggregate index names, reduced into `summary.json`.
The summary format is pinned in `contracts/workload/README.md`.

## Use

`python -m wdf_aggregate run` reads the results of an aggregate index from the store, and `python -m wdf_aggregate files` reads local files named by task id.
Each result is reduced on its own and the partial results are combined in ascending task id, so the bytes of `summary.json` depend only on the set of inputs.
The exit codes are those of the aggregate pool in `contracts/workload/exit-classes.yaml`.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/aggregate -q -rs
```
