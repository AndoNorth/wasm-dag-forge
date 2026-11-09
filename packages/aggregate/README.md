# wdf-aggregate

The aggregate task: the `results.xml` files that an aggregate index names, reduced into `summary.json`.
The summary format is pinned in `contracts/workload/README.md`.

## State

Every module is a stub.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/aggregate -q -rs
```
