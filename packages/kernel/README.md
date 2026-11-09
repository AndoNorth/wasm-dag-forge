# wdf-kernel

The deterministic kernel of the workloads: the generators, the expected blocks and digests, and the `wdf.sim.v1` format rules that the host checks and the guest share.
The format it follows is pinned in `contracts/workload/README.md`.

## State

`format` holds the value formats and the document rules of `wdf.sim.v1`, and the contract loader of `wdf_runtime` calls it for `results.xml`.
`expected` and `check` are stubs.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Standard library only

The same modules run on host Python 3.13 and inside a guest on CPython 3.14, where no package is installed.
The package declares no dependency, and `tests/test_stdlib_only.py` fails on any import outside the standard library.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/kernel -q -rs
```
