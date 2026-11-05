# wdf-artifactclient

The artifact client library of the artifact passing epic, and the loader of the artifact contract files.
The contract it loads is pinned in `contracts/artifact/README.md`.

## State

The contract loader, the models and the schema generator are delivered.
The modules of the client, the checkpoints, the aggregate index, the compile cache and the fault hooks are stubs.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## Testing helpers

`wdf_artifactclient.testing` is a pytest plugin, registered by the package entry point.
It offers a stand-in guest runner and the fixtures `scratch` and `runner`.
A guest is a callable that reads `input/` and writes `output/`.
A real runner joins the staging tests by adding itself to `RUNNERS`.

## Commands

```sh
nix develop -c uv run --frozen python -m wdf_artifactclient.contract --check contracts/artifact/failures.yaml
nix develop -c uv run --frozen python -m wdf_artifactclient.gen --check
nix develop -c uv run --frozen pytest packages/artifactclient -q -rs
```
