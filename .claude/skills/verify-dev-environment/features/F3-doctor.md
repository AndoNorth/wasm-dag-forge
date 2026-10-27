# F3 doctor

Tier: T0, T1 | Epic: #1 | Status: planned | Needs: lane C (#30)

## Sub-features

- `just doctor` and its fault-injection self-test

## How to get to it (user POV)

A contributor or CI uses `just doctor` and its fault-injection self-test.

## Driving it with bash and nix develop

Not scripted yet.
Spec: docs/epics/01-dev-environment.md section 6, feature map row F3.
Script to add: `scripts/verify/dev-environment/F3-doctor.sh`, then set Status to scripted.

## Gotchas

- Planned until lane C (#30) merges (doctor.sh and doctor.test.sh).
  The journey is not weakened to pass against a stub.
