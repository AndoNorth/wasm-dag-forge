# F4 python

Tier: T0 | Epic: #1 | Status: planned | Needs: lane B (#29)

## Sub-features

- `uv lock`, `uv sync --frozen` and Python provenance

## How to get to it (user POV)

A contributor or CI uses `uv lock`, `uv sync --frozen` and Python provenance.

## Driving it with bash and nix develop

Not scripted yet.
Spec: docs/epics/01-dev-environment.md section 6, feature map row F4.
Script to add: `scripts/verify/dev-environment/F4-python.sh`, then set Status to scripted.

## Gotchas

- Planned until lane B (#29) merges (scripts/env.sh and uv workspace).
  The journey is not weakened to pass against a stub.
