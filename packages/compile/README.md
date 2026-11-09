# wdf-compile

The compile task: a source bundle and its locked dependencies built into a workload component with `componentize-py`.
The bundle and build info formats are pinned in `contracts/workload/README.md`.

## State

Every module is a stub.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Toolchain

The package requires the `componentize-py` version that `wdf_runtime.pins` names, and `uv.lock` holds it with the hash of every wheel.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/compile -q -rs
```
