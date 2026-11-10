# wdf-workloads

The workloads that a simulate task runs inside the guest, and the bundle that the compile task builds them from.
The guest contract they follow is pinned in `contracts/workload/README.md`.

## State

`wdf_workloads.main` is the entry module, and its `Run` export reads `/in/input.json` and dispatches on `workload`.
An unknown workload writes `/out/error.json` with the code `unknown-workload` and exits 1.
The level 0 workloads of `level0.py` write the blocks that `wdf_kernel` generates to `results.xml`.
`level1.py` and `fault.py` are stubs, which raise `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Commands

```sh
nix develop -c uv run --frozen python -m wdf_workloads.bundle --out DIR
nix develop -c uv run --frozen pytest packages/workloads -q -rs
```
