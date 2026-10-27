---
name: verify-dev-environment
description: Verify the wasm-dag-forge Nix dev shell, the just recipe table, doctor, Python bootstrap, lint wiring and CI parity from a clean export of the repo. Use after any change to flake.nix, justfile, just/, scripts/ or the CI workflow, and before closing an epic #1 lane.
---

# verify-dev-environment

Proves the dev environment epic (#1) by driving the real `nix develop` shell and `just` against a clean export of the repository.
Spec: [docs/epics/01-dev-environment.md](../../../docs/epics/01-dev-environment.md) section 6.
Method: [docs/method.md](../../../docs/method.md#the-verifier-convention).

## Surface and tiers

- Surface: CLI.
  `nix develop`, `just` and bash against an exported tree.
  Nothing interactive, no browser, no PTY.
- T0: fixtures and real tools in a scratch export, no runtime.
  F1 to F7 are T0.
- T1: only because doctor (F3) reads the real Docker engine and the host.
- T2: not used.
  The kind cluster belongs to #2.

## Isolation

- Slot: `WDF_SLOT` (default `0`), part of the run id.
- Ports: none bound, nothing listens.
- Lock: `/tmp/wdf-verify-dev-environment.lock`, taken with `flock -n`.
  A second run refuses to start while it is held, so two runs never coexist.
- Scratch: `${WDF_VERIFY_TMP:-/tmp}/wdf-verify-dev-environment/<run id>/`, removed at cleanup.
- Refusal rule: a run only drives the tree it exported itself.
  It never touches the shared kind cluster or any container.

## Launch

```bash
just verify dev-environment            # all features
just verify dev-environment F1 F2      # selected features
just verify dev-environment --dirty    # export working tree instead of HEAD
```

Launch creates the scratch run directory and exports the committed state with `git archive HEAD | tar -x` (what CI sees).
`--dirty` copies `git ls-files -co --exclude-standard` instead, for checking uncommitted work.
Ready means `nix develop <run dir>/tree -c just --version` exits 0.
No server starts and no data directory exists.

## Doctor

```bash
scripts/verify/dev-environment/doctor.sh
```

Read-only.
Prints `ok|warn|fail <id>: <detail>` for nix with flakes, Docker reachable, 5 GiB free on the store filesystem, the lock not held by another run, and, after launch, the run directory owned by the caller and `flake.lock` unchanged since launch.
`WDF_VERIFY_SKIP_DOCKER=1` downgrades the Docker check to a warning, since T0 features never touch Docker.
Run it first whenever a run looks wrong.

## Drive

Each scripted feature is one script, `scripts/verify/dev-environment/F<n>-<slug>.sh`, driven by `run.sh` with the exported tree.
The feature file says what it proves and why: [features/README.md](features/README.md).
Every command runs through `nix develop <tree> -c ...`, so the proof exercises the real shell and never a mock of it.
A check is `pass`, `fail` or `skip <reason>`.
A skip is for a counterpart another lane has not merged yet and is listed in the evidence, never silent.

## Evidence

`.verify-evidence/dev-environment/<run id>/` (gitignored), `<run id>` = `<UTC timestamp>-<slot>`:

- `summary.json`: run id, commit sha, tree kind, and per feature the tier, result, exit and skip reasons
- `doctor-pre.txt`, `doctor.txt`, `launch.txt`
- `F<n>/`: `cmd.txt`, `stdout.txt`, `stderr.txt`, `exit`, `checks.txt` and the side effect that proves it (`which-a.txt` and `drvnames.txt` for F1)

Proof standard: the real shell, the real recipes, the command and its result captured together.
Paste `summary.json` into the PR.

## Cleanup

Automatic on exit (also after a failed run): `clean.sh` removes the scratch run directory only and the lock is released.
Never `nix-collect-garbage`, never kill by name, never remove evidence.
After cleanup the evidence directory still exists, and `run.sh` prints its path last.

## Helpers

All under `scripts/verify/dev-environment/`:

| Script | Invocation | Does |
| --- | --- | --- |
| `run.sh` | `just verify dev-environment [--dirty] [Fn ...]` | launch, doctor, drive, evidence, cleanup |
| `doctor.sh` | `doctor.sh [run dir]` | read-only host and run check |
| `export.sh` | `export.sh <run dir> [--dirty]` | export the tree, record commit and `flake.lock` hash |
| `clean.sh` | `clean.sh <run dir>` | remove the scratch run directory, refuses any other path |

`just verify-lint` checks this skill's map against the repo (feature file shape, scripted features have scripts, every recipe is covered or allowlisted in [verify-allowlist.txt](verify-allowlist.txt)).

## Feature status

Source of truth for each row is the metadata line in the feature file, and `verify-lint` fails if they differ.

| ID | Feature | Tier | Epic | Status |
| --- | --- | --- | --- | --- |
| F1 | [shell-pins](features/F1-shell-pins.md) | T0 | #1 | scripted |
| F2 | [recipes](features/F2-recipes.md) | T0 | #1 | scripted |
| F3 | [doctor](features/F3-doctor.md) | T0, T1 | #1 | planned |
| F4 | [python](features/F4-python.md) | T0 | #1 | planned |
| F5 | [lint](features/F5-lint.md) | T0 | #1 | planned |
| F6 | [ci-parity](features/F6-ci-parity.md) | T0 | #1 | planned |
| F7 | [reproducible](features/F7-reproducible.md) | T0 | #1 | planned |

Not verifiable here and not in the map: `up`, `down`, `nuke`, `cluster-verify` (#2), `smoke`, `e2e` (#17), Docker Desktop, non-WSL Linux, macOS, and cold binary-cache behaviour, which needs a clean Nix store that must not be created on the shared host.
