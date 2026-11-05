# wdf-artifactreaper

The reaper of the artifact passing epic: retention of artifact objects and their index rows.

## State

Every module is a stub.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Contract files

The package carries the layout and the failure taxonomy of `contracts/artifact/` as package data.
The retention windows are added with the code that reads them.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/artifactreaper -q -rs
```
