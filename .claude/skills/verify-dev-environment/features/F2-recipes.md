# F2 recipes

Tier: T0 | Epic: #1 | Status: scripted

## Sub-features

- `just --summary` lists the 14 contract names and only `verify` and `verify-lint` beyond them
- the stubs `up down nuke cluster-verify smoke e2e load-test characterize` exit 3 with `not implemented: owned by #N`
- `test itest lint ci doctor versions` call `scripts/<name>.sh`
- every `just/*.just` module is imported by the justfile
- `just versions` exits 0, or is reported as a stub

## How to get to it (user POV)

A contributor types `just --list` and uses the recipes listed there as the only entrypoint.

## Driving it with bash and nix develop

`just verify dev-environment F2` runs `scripts/verify/dev-environment/F2-recipes.sh`.
It runs each stub recipe and checks the exit code and the owner text, and reads recipe bodies with `just --show`.
Proof: `pass contract-names`, `pass extra-names`, one `pass stub-<recipe>` per stub in `checks.txt`.

## Gotchas

- The contract is 14 names, so `verify` and `verify-lint` make 16.
  This deviates from issue 28's 'exactly 14', and the check allows exactly those two extras.
- Stub checks hold through epic #1 only.
  When a later epic implements a stub, move that recipe to the real-recipe loop in the same PR.
- `just versions` is a stub until lane E merges and reports `skip` until then.
