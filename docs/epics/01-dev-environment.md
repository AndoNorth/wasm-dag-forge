# Epic #1: Dev environment (Nix) - breakdown

Status: approved 2026-10-27. Updated 2026-10-27: the verifier (lane V.1, `verify-dev-environment`) was dropped and the dev environment is proven by `just doctor` with its self-test, the lint fixtures, the recipe checks, the CI run of `just ci` and `docs/dev-setup.md`. Issues 0.1, 0.2 and B.1 are merged and the rest are open.
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/1
Architecture note: docs/architecture/08-dev-environment.drawio
Verifier: none, see section 6
Depends on (closed epics): nothing

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-10-27) while preparing this spec, `[read]` means read and not re-run.
Epic line references (`#1 L59`) are lines of the epic issue body as read on 2026-10-27, before the corrections in section 0.1 are applied.
Method: [docs/method.md](../method.md).

## 0. Decisions

All four decisions below were answered at approval and are applied in this spec.

| # | Question | Decision |
| --- | --- | --- |
| 1 | Is the `kind smoke` CI job part of #1 or #2? | Moved to #2's exit and is that epic's acceptance. #1 closes with the lint, test and flake-check jobs. |
| 2 | Is `docs/dev-setup.md` in #1? The README links it and it does not exist. | Written in #1, lane V. |
| 3 | The README and AGENTS.md line 4 still say "run as WASM" (consistency finding C-1). | Left alone here. A separate follow-up owns it. |
| 4 | `wasmtime` and `wasm-tools`: keep the hash-checked fetch? | Kept. nixos-26.05 carries wasmtime 45.0.2 and wasm-tools 1.249.0 [ran: `nix eval`], and #1 pins wasmtime 48.0.7. |

### 0.1 Corrections to apply to the epic issues

Preparing this spec found text errors in the epic issues.
They are recorded here and applied to the issues by the consistency edit pass, not edited by this spec.

1. #1 L62 (`path-provenance`) says doctor "asserts every tool resolves under `/nix/store`".
   That cannot detect shadowing: outside the shell, `kind`, `kubectl` and `helm` resolve through `~/.nix-profile`, and `readlink -f` lands in `/nix/store` anyway [ran].
   The check must be that the first match on PATH is a flake path and not a profile path, and doctor must refuse to run outside the shell (issue C.1).
2. #1 L59 sets `UV_PYTHON_DOWNLOADS=never`, which does not keep uv on the flake's Python.
   With a uv-managed `cpython-3.13.14` in `~/.local/share/uv/python`, `uv lock` and `uv sync` picked it over the flake's 3.13.15.
   `UV_PYTHON_PREFERENCE=only-system` must be set alongside it, and selected the flake's Python [ran] (issue B.1).
3. #2 L82 says `fs.inotify.max_user_instances` is 128 "here".
   It is 512 [ran], so the text is stale and the doctor threshold is 512.
4. #1 detail issue 6 includes a kind smoke job on the `ci` preset.
   It cannot be green before #2, so it moves to #2's exit (decision 1).
5. #1 detail issue 7 puts `componentize-py` in `uv.lock` now.
   There is no consumer until #11 and `uv.lock` has one owner per epic, so issue E.1 defers it to #11 (E7).
   This is a recommendation carried from the scout report that the approval did not separately confirm, so it is flagged for the consistency edit pass.

## 1. Goal and non-goals

Goal: one Nix flake pins every tool, the same commands run on a laptop, a second contributor and CI, and `just` is the single entrypoint (#1 L3 to L16 [read]).
Non-goals for this pass:

- everything #1 lists under "Does not own"
- the recipes `up`, `down`, `nuke`, `cluster-verify`, `smoke`, `e2e`, `load-test` and `characterize`, which exist as stubs only
- the kind smoke CI job, moved to #2
- the "run as WASM" wording in the README and AGENTS.md

## 2. Interfaces

### Consumes

Only host prerequisites: Nix with flakes, Docker engine, WSL2 with systemd and cgroup v2, repo on ext4.
No epic is consumed.

Host facts checked [ran]: Nix 2.35.2 with `experimental-features = nix-command flakes`, Docker 29.1.3 with `CgroupVersion 2`, PID 1 is systemd, kernel `6.6.87.2-microsoft-standard-WSL2`, `fs.inotify.max_user_instances` 512, repo on ext4 (`/dev/sdd`).

### Produces

| To | What | Path | Shape | Version |
| --- | --- | --- | --- | --- |
| all epics | Dev shell | `flake.nix`, `flake.lock`, `.envrc` | `devShells.x86_64-linux.default` via `mkShellNoCC`, nixpkgs `nixos-26.05`. Packages (14): kind, ctlptl, kubectl, kubernetes-helm, tilt, just, uv, python313, ruff, yamllint, shellcheck, actionlint, kubeconform, nixfmt. Plus `wasmtime` and `wasm-tools` from `nix/wasm-tools.nix` (hash-checked release). `shellHook` only runs `source scripts/env.sh`. | lock |
| all epics | Tool versions at the draft lock (nixpkgs `718f670e`, 2026-10-26 [ran]) | `flake.lock` | kind 0.31.0, ctlptl 0.9.3, kubectl 1.36.3, helm 3.20.2, tilt 0.37.5, just 1.51.0, uv 0.11.21, python 3.13.15, ruff 0.15.14, yamllint 1.37.1, shellcheck 0.11.0, actionlint 1.7.12, kubeconform 0.7.0, nixfmt 1.5.0 | lock |
| all epics | Recipe table | `justfile` plus `just/<domain>.just` | 14 names: `up down nuke cluster-verify test itest lint smoke e2e load-test characterize ci doctor versions`. An unimplemented recipe prints `not implemented: owned by #N` and exits 3. `test` runs `pytest -m "not integration"` and `itest [domain]` runs `pytest -m integration`, with pytest exit 5 (nothing collected) turned into exit 0 with a notice. `ci` = `lint` + `test` + `itest` + `nix flake check`. | recipe table v1 |
| #2 | `just doctor [--json]` | `scripts/doctor.sh` | One line per check `ok\|warn\|fail <id>: <detail>`, exit 0 if no `fail` and 1 otherwise, and `--json` prints `[{id,status,detail}]`. Ids: `nix-flakes`, `docker-engine`, `docker-version`, `cgroup-v2`, `wsl-systemd`, `repo-on-ext4`, `inotify-instances`, `memory-vs-preset`, `path-provenance`, `in-dev-shell`. Refuses with `fail in-dev-shell` outside the flake shell. | doctor contract v1 |
| #2, #3, #6, #7, #13 | `deploy/versions.yaml` | `deploy/versions.yaml`, `contracts/versions.schema.json` | `kind_node_image`, `charts`, `images` maps, each entry `{version, digest}`. `just versions` prints tool versions and this file. One key per line. | schema v1 |
| all Python epics | uv workspace | `pyproject.toml`, `.python-version`, `uv.lock`, `scripts/env.sh`, `packages/<name>/` | `requires-python = ">=3.13,<3.14"`, `[tool.uv.workspace] members = ["packages/*"]`, `UV_PYTHON_DOWNLOADS=never`, `UV_PYTHON_PREFERENCE=only-system`, never `UV_PYTHON`, plus an `LD_LIBRARY_PATH` shim for manylinux wheels | workspace v1 |
| CI | Workflow | `.github/workflows/ci.yml` | one job `check` running `nix develop -c just ci` | v1 |

Checked [ran]: an empty `packages/` with `members = ["packages/*"]` is accepted by `uv lock` (resolved 1 package), `uv sync --frozen` creates the venv, and `numpy 2.5.4` imports from it on the author's host.

## 3. Lanes (disjoint file ownership)

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Skeleton | 1 | `flake.nix`, `flake.lock`, `.envrc`, `justfile`, `just/*.just` (until lanes take over), stub `scripts/*.sh` | 0.1, 0.2 | nothing (barrier) |
| B Python | 1 | `pyproject.toml`, `.python-version`, `uv.lock`, `scripts/env.sh`, `packages/` | B.1 | lane 0 |
| C Doctor | 1 | `scripts/doctor.sh`, `scripts/doctor.test.sh`, `scripts/fixtures/doctor/**`, `just/doctor.just` | C.1 | lane 0 |
| D Quality | 1 | `ruff.toml`, `.yamllint`, `.shellcheckrc`, `.editorconfig`, `scripts/lint.sh`, `scripts/test.sh`, `scripts/itest.sh`, `scripts/ci.sh`, `scripts/fixtures/lint-bad/**`, `.github/workflows/ci.yml`, `just/quality.just` | D.1, D.2 | lane 0 (D.2 also needs B.1 for pytest) |
| E Pins | 1 | `nix/wasm-tools.nix`, `deploy/versions.yaml`, `contracts/versions.schema.json`, `scripts/versions.sh`, `docs/versions-policy.md` | E.1, E.2 | lane 0 (E.1 touches `flake.nix` only through one `import`) |
| V Docs and reproducibility | 1 | `docs/dev-setup.md`, the CI reproducibility step, `.gitignore` | V.2 | lanes B to E merged, written last |

Shared-file rules:

- `flake.nix` is lane 0's.
  Lane E adds its package by creating `nix/wasm-tools.nix`, which lane 0's flake already imports behind `lib.optionals (builtins.pathExists ./nix/wasm-tools.nix)`.
  Lane 0 ships the conditional import, so E.1 changes no lane 0 file.
- `uv.lock` is lane B's, and no other lane adds a dependency in this epic.
- `.gitignore` is lane V's (`.venv/`).

## 4. Issues

Epic proposal numbering is in brackets.
Every acceptance line is a command with an observable result.

**0.1 Flake dev shell** [1, 9] Lane 0, tier T0. Status: merged.

- `nix flake check` exits 0. (Draft: passes [ran].)
- `nix develop -c bash -c 'command -v <each of the 14 tools>'` prints a `/nix/store/...` path for each. (Draft: all 14 [ran].)
- `nix develop -c helm version --short` starts with `v3`. (Draft: `v3.20.2` [ran].)
- `flake.lock` is committed and pins `nixos-26.05`.
- Cold realisation time is recorded in the PR. (Draft: 25.1 s wall with warm network [ran], and a truly cold store is about 886 MiB per #1 L60 [read].)
- `.envrc` contains `use flake` and nothing else.
- Proof: the acceptance commands above, run by the worker and kept in the PR.

**0.2 justfile skeleton** [2] Lane 0, T0. Status: merged.

- `just --list` shows exactly the 14 names.
- Each unimplemented recipe exits 3 and prints `not implemented: owned by #N` with the right N: `up down nuke cluster-verify` are #2, `smoke e2e` are #17, `load-test` is #14, `characterize` is #16.
- Every real recipe calls `scripts/<name>.sh`, no logic lives in the justfile, and `just/` modules are imported by the root file.
- Proof: `just --list` and each stub recipe run in the PR, which is the recipe check.

**B.1 Python bootstrap** [4] Lane B, T0. Status: merged.

- `nix develop -c uv lock` exits 0 with an empty `packages/`, and `uv sync --frozen` creates `.venv`. (Draft [ran].)
- `.venv/bin/python --version` equals the flake's `python3 --version` (3.13.15), not a uv-managed Python.
  Regression to add: with a `cpython-3.13.14` present in `~/.local/share/uv/python`, uv picked it unless `UV_PYTHON_PREFERENCE=only-system` was set [ran].
  The criterion fails without that variable.
- `UV_PYTHON` is unset in the shell (asserted, since #1 L59 says it breaks `uv pip`).
- A scratch package with a locked `numpy` imports in the shell.
  On the author's host it imports without the shim [ran], so the shim criterion is "documented, and the check passes both with and without `LD_LIBRARY_PATH` set".
  The shim is verified on a non-NixOS image only if the epic keeps that claim (open).
- Proof: the uv provenance regression above and the scratch `numpy` import.

**C.1 `just doctor`** [3] Lane C, T0 (self-test) and T1 (live). Status: open.

- On the author's host: exit 0, printing the version of nix, docker, kernel and the `inotify` limit. (All inputs readable [ran].)
- `path-provenance`: for each of the 14 tools, the first match on PATH is under a flake path and not under `$HOME/.nix-profile` or `/nix/var/nix/profiles`, and `which -a` is printed when it fails.
  Reason: outside the shell `kind`, `kubectl` and `helm` resolve via `~/.nix-profile` and `readlink -f` ends in `/nix/store` [ran], so a plain "under `/nix/store`" check would pass while shadowing.
- Outside the shell: exit 1 with `fail in-dev-shell`.
- The `inotify-instances` threshold is 512.
- Self-test `scripts/doctor.test.sh` injects each fault with fake commands and files (`WDF_DOCTOR_ROOT` points at a fixture tree): no docker, cgroup v1, repo under `/mnt/c`, `inotify` below 512, shadow `helm` first on PATH, flakes disabled, memory below the preset.
  Each makes doctor exit 1 and names exactly that check id, and the healthy fixture exits 0.
- `memory-vs-preset` reads `deploy/presets/<WDF_PRESET>.yaml` if it exists (#2 writes it), else prints `warn memory-vs-preset: no preset file yet` and does not fail.
- Proof: `scripts/doctor.test.sh`, the fault-injection self-test.

**D.1 Lint and format wiring** [5] Lane D, T0. Status: open.

- `just lint` exits 0 on the repo and runs ruff (check and format --check), yamllint, shellcheck, actionlint, kubeconform (on `deploy/**` if present) and nixfmt (check) on the files each owns.
- `scripts/fixtures/lint-bad/` holds one bad file per linter, and `scripts/lint.sh --expect-fail <file>` exits 0 only if that linter fails on it.
  Lane D's test runs all six.
- Lint config lives in the files lane D owns only, with no `[tool.ruff]` in `pyproject.toml`.
- Proof: `scripts/lint.sh --expect-fail` over the six lint fixtures.

**D.2 CI workflow and test recipes** [6] Lane D, T0. Status: open.

- `.github/workflows/ci.yml` has one job whose only run step is `nix develop -c just ci` (checked by `actionlint`).
- `just test` and `just itest` exit 0 with nothing collected and print the notice.
  With a trivial passing test dropped in a scratch package they exit 0, and with a failing one exit 1 (checked in scratch, not committed).
- No kind smoke job is present (moved to #2's exit).
- Evidence outside the sandbox: one green CI run URL on the PR.
- Proof: the CI `check` run on the PR and `just ci` run locally.

**E.1 WASM toolchain pins** [7] Lane E, T0. Status: open.

- `nix develop -c wasmtime --version` prints `48.0.7` and `wasm-tools --version` prints the pinned version.
  The fetch is hash-checked: corrupting the hash in `nix/wasm-tools.nix` makes `nix develop` fail with a hash mismatch.
  (Pins are read from #1 L11 and not run. nixos-26.05 itself carries wasmtime 45.0.2 and wasm-tools 1.249.0 [ran: `nix eval`].)
- `componentize-py` is not pinned here, because `uv.lock` is lane B's and the package is first needed by #11.
  `docs/versions-policy.md` records that #11 adds a hash-pinned entry to `uv.lock` in E7.
  This deviates from #1 detail issue 7 (correction 5 in section 0.1).
- Proof: the corrupt-hash check, which makes `nix develop` fail with a hash mismatch in a scratch copy.

**E.2 `deploy/versions.yaml` and bump policy** [8] Lane E, T0. Status: open.

- `contracts/versions.schema.json` validates `deploy/versions.yaml` (`just versions --check`, exit 1 on a missing digest).
- `just versions` prints the 14 tool versions from the shell and the file's entries.
- `docs/versions-policy.md` states that a tool bump is a `flake.lock` update, a chart or image bump is one edit, and first-party charts are preferred (#1 L63).
- Proof: `just versions --check` exits 1 on a missing digest and 0 on the committed file.

**V.2 `docs/dev-setup.md`, reproducibility and `.gitignore`** [issue 36] Lane V, T0. Status: open.

- A reader with only the host prerequisites reaches `just doctor` green by following the file, tested by running its commands from a clean export.
- The file ends with a short "what green looks like" checklist: `just doctor` exit 0, `just ci` exit 0, `just --list` showing 14 names and the CI `check` job green.
- One short CI step exports the repo twice and checks that `nix eval .#devShells.x86_64-linux.default.drvPath` gives the same path for both.
- The README link resolves.
- `.gitignore` contains `.venv/`.

## 5. Order, stubs and spikes

- Barrier: lane 0 (0.1 then 0.2, one worker, merged before anything else).
  The estimate is half a day, since the flake is 12 lines and already evaluates [ran].
- After the barrier: B, C, D and E start together, four workers.
  D.2 waits for B.1 only to run pytest, and can write against the stub `scripts/test.sh` meanwhile.
  Lane V (docs and reproducibility) is last, after the others merge.
- Stubs: the `scripts/*.sh` stubs from lane 0 are the stubs, and the conformance test is the recipe check (`just --list` and each stub exit code), which runs against stub and real script alike.
- Spikes: none.
- Overlap with #2: the kind smoke job is not built here and is #2's exit acceptance.

## 6. Proof without a verifier

The dev environment has no `verify-dev-environment` skill.
Its surface is a shell, a command table and a host preflight, so the proof is made of checks the lanes already ship.
The verifier convention stays for domains that must be driven end to end (see [docs/method.md](../method.md#the-verifier-convention)).

| Lane | What proves it |
| --- | --- |
| 0 Skeleton | `nix flake check`, the 14 tool paths under `/nix/store`, and the recipe check (`just --list` and each stub exit 3 with its owner text) |
| B Python | the uv provenance regression (`.venv` Python equals the flake's with a uv-managed Python present) and the scratch `numpy` import |
| C Doctor | `just doctor` on the host, and `scripts/doctor.test.sh`, the fault-injection self-test |
| D Quality | the six lint fixtures through `scripts/lint.sh --expect-fail`, and the CI `check` run of `just ci` |
| E Pins | the corrupt-hash check on `nix/wasm-tools.nix` and `just versions --check` |
| V Docs | commands in `docs/dev-setup.md` run from a clean export, and the CI reproducibility step (two exports, one `drvPath`) |

Honest limit: the exit test cannot prove "a second contributor's laptop".
The reproducibility step is its stand-in (same derivation path, same lock).
The setup steps a real second machine would do stay unverified until a machine other than the author's host runs the doctor: installing Nix, enabling flakes, installing Docker.

Not yet verifiable:

- `just up`, `down`, `nuke`, `cluster-verify` (#2)
- `smoke`, `e2e` (#17), `load-test`, `characterize`
- Docker Desktop, non-WSL Linux, macOS
- cold binary-cache behaviour, which needs a clean Nix store that a worker must not create on the shared host

## 7. Exit test

Run by a worker who did not write the lanes, on a clean export of `main`.
The command output is kept in the PR that records the run.

| # | Command | Expected |
| --- | --- | --- |
| 1 | `nix flake check` and `nix develop -c just versions` | exit 0, 16 tool versions and the pins file printed, helm major 3, wasmtime 48.0.7 |
| 2 | `just doctor` in the shell, then `scripts/doctor.test.sh` | exit 0 on the host, eight injected faults each exit 1 naming one check |
| 3 | `just doctor` outside the shell (`env -i`) | exit 1, `fail in-dev-shell` |
| 4 | `just lint` and the six bad fixtures | clean, then six distinct failures |
| 5 | `just --list`, then each stub recipe | 14 names, stubs exit 3 with owner |
| 6 | `just test`, `just itest`, `just ci` | exit 0 (nothing to collect, notice printed) |
| 7 | two exports, `nix eval ...drvPath`, as the CI reproducibility step does | identical |
| 8 | `uv sync --frozen`, then Python provenance | `.venv` Python is the flake's, not `~/.local/share/uv/python/...` |

Plus outside the host: one green `check` job run on the PR, with its URL in Closure.
CI was not run while preparing this spec.

## 8. Parallelism and cost

- Maximum parallel workers: 4 (lanes B, C, D, E), after a one-worker barrier, then lane V alone.
- Host contention: none.
  Nothing here binds a port or starts a cluster.
  Four worktrees running `nix develop` share the store, a cold shell was 25 s [ran], and the first full realisation is about 886 MiB once (#1 L60).
- Critical path, as estimates and not measurements: lane 0 (0.5 d), the longest lane C (1 d, the self-test with fault injection), lane V and the exit test (0.5 d).
  That is about 2 days elapsed and 5 worker-days of effort.

## 9. Closure (filled at close)

Epic closed.
The exit test passed 8 of 8 rows.
Green CI run of `just ci` on `main`: https://github.com/AndoNorth/wasm-dag-forge/actions/runs/40600173571

Merged pull requests:

- Shell and justfile: flake dev shell and justfile skeleton.
- uv workspace: uv workspace, python pin and manylinux shim.
- Doctor: `just doctor` with its fault-injection self-test.
- Lint and CI: lint, CI workflow and test recipes.
- Version pins: WASM toolchain pins and versions file.
- Docs: dev setup guide and reproducibility check, and the verifier removal.
- Hygiene: issue references dropped from comments and an evidence-based PR template.
- Exit-test fixes: lint and test no longer give a false green outside a git checkout.

Follow-ups left:

- `kubeconform` fetches schemas from the network.
- `just --list` lacks descriptions.
- A second contributor machine is unverified.

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).

- 2026-10-28: recipe table v2 and the `deploy/presets/` lint exclusion, see https://github.com/AndoNorth/wasm-dag-forge/issues/49.
