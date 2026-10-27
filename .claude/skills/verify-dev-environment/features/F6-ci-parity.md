# F6 ci-parity

Tier: T0 | Epic: #1 | Status: planned | Needs: lane D (#32)

## Sub-features

- `ci.yml` shape, `just test`, `just itest` and `just ci`

## How to get to it (user POV)

A contributor or CI uses `ci.yml` shape, `just test`, `just itest` and `just ci`.

## Driving it with bash and nix develop

Not scripted yet.
Spec: docs/epics/01-dev-environment.md section 6, feature map row F6.
Script to add: `scripts/verify/dev-environment/F6-ci-parity.sh`, then set Status to scripted.

## Gotchas

- Planned until lane D (#32) merges (ci.yml and the test recipes).
  The journey is not weakened to pass against a stub.
