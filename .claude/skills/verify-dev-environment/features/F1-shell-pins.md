# F1 shell-pins

Tier: T0 | Epic: #1 | Status: scripted

## Sub-features

- each of the 14 flake tools resolves from the flake and not from a profile
- each tool's version equals the derivation version in the lock
- `helm` is major 3
- `nix flake check` exits 0

## How to get to it (user POV)

A contributor runs `nix develop` (or `direnv` with `.envrc`) and expects `kind`, `kubectl`, `helm`, `just` and the rest to be the pinned ones.

## Driving it with bash and nix develop

`just verify dev-environment F1` runs `scripts/verify/dev-environment/F1-shell-pins.sh`.
Per tool it records `command -v` and `type -aP` (the first match must be under `/nix/store`, not `~/.nix-profile` or `/nix/var/nix/profiles`) into `which-a.txt`, and compares the tool's own version output with the derivation name from `nix eval ...nativeBuildInputs` (`drvnames.txt`).
Proof: every `pass path-*` and `pass version-*` line in `checks.txt`, plus `pass flake-check`.

## Gotchas

- `wasmtime`, `wasm-tools`, the corrupt-hash failure and `just versions` against `deploy/versions.yaml` depend on lane E (`nix/wasm-tools.nix`, `deploy/versions.yaml`).
  They report `skip` until those files exist, and once `nix/wasm-tools.nix` exists the script fails on purpose until it is extended.
  The spec's 16 tools are therefore 14 checked now.
- The first PATH match is checked inside `nix develop`, so outside-the-shell shadowing is F3's job.
