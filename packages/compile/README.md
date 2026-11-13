# wdf-compile

The compile task: a source bundle and its locked dependencies built into a workload component with `componentize-py`.
The bundle and build info formats are pinned in `contracts/workload/README.md`.

## State

The bundle format, the build, `python -m wdf_compile build|info`, the offline fetch of `wdf_compile.limits` and the heavy bundle generator of `wdf_compile.heavy` are delivered.
No module has a stub, so every `STUBBED` is empty, and `tests/test_stubs.py` fails on a name that raises the stub error without being listed there.

## Toolchain

The package requires the `componentize-py` version that `wdf_runtime.pins` names, and `uv.lock` holds it with the hash of every wheel.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/compile -q -rs
```
