# wdf-runtime

The models and the loader of the workload contract, the toolchain pins, and the runtime that runs a guest with `wasmtime`.
The contract it loads is pinned in `contracts/workload/README.md`.

## State

The models, the loader `python -m wdf_runtime.contract --check FILE`, the schema generator `python -m wdf_runtime.gen [--check]` and the pins are delivered.
`command`, `classify`, `calibrate` and `python -m wdf_runtime run` are stubs.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Pins

`wdf_runtime.pins` names the `componentize-py`, WIT and `wasmtime` versions of a cache key and a guest run, and the guest memory figures that the S6 spike measured.
`GUEST_OVERHEAD_MIB` is what `wasmtime` holds beyond the guest's linear memory in a warm run, and `GUEST_MIN_PAGES` is the initial memory that the workload component declares.
`tests/test_pins.py` holds each version equal to `uv.lock`, the installed packages and `nix/wasm-tools.nix`.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/runtime -q -rs
nix develop -c uv run --frozen python -m wdf_runtime.contract --check FILE
```
