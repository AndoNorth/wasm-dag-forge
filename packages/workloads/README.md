# wdf-workloads

The workloads that a simulate task runs inside the guest, and the bundle that the compile task builds them from.
The guest contract they follow is pinned in `contracts/workload/README.md`.

## State

Every module is a stub.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/workloads -q -rs
```
