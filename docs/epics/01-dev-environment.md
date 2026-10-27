# Epic #1: Dev environment (Nix) - breakdown

Status: approved 2026-10-27
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/1
Architecture note: docs/architecture/08-dev-environment.drawio
Verifier: .claude/skills/verify-dev-environment/
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
| all epics | Recipe table | `justfile` plus `just/<domain>.just` | 14 names: `up down nuke cluster-verify test itest lint smoke e2e load-test characterize ci doctor versions`. An unimplemented recipe prints `not implemented: owned by #N` and exits 3. `test` runs `pytest -m "not integration"` and `itest [domain]` runs `pytest -m integration`, with pytest exit 5 (nothing collected) turned into exit 0 with a notice. `ci` = `lint` + `test` + `itest` + `nix flake check` + `verify-lint`. | recipe table v1 |
| #2, `verify-*` | `just doctor [--json]` | `scripts/doctor.sh` | One line per check `ok\|warn\|fail <id>: <detail>`, exit 0 if no `fail` and 1 otherwise, and `--json` prints `[{id,status,detail}]`. Ids: `nix-flakes`, `docker-engine`, `docker-version`, `cgroup-v2`, `wsl-systemd`, `repo-on-ext4`, `inotify-instances`, `memory-vs-preset`, `path-provenance`, `in-dev-shell`. Refuses with `fail in-dev-shell` outside the flake shell. | doctor contract v1 |
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
| V Verifier and docs | 1 | `.claude/skills/verify-dev-environment/**`, `scripts/verify/dev-environment/**`, `scripts/verify-lint.sh`, `just/verify.just`, `docs/dev-setup.md`, `.gitignore` | V.1, V.2 | skeleton at lane 0, finalised last |

Shared-file rules:

- `flake.nix` is lane 0's.
  Lane E adds its package by creating `nix/wasm-tools.nix`, which lane 0's flake already imports behind `lib.optionals (builtins.pathExists ./nix/wasm-tools.nix)`.
  Lane 0 ships the conditional import, so E.1 changes no lane 0 file.
- `uv.lock` is lane B's, and no other lane adds a dependency in this epic.
- `.gitignore` is lane V's (`.verify-evidence/`, `.venv/`).

## 4. Issues

Epic proposal numbering is in brackets.
Every acceptance line is a command with an observable result.

**0.1 Flake dev shell** [1, 9] Lane 0, tier T0.

- `nix flake check` exits 0. (Draft: passes [ran].)
- `nix develop -c bash -c 'command -v <each of the 14 tools>'` prints a `/nix/store/...` path for each. (Draft: all 14 [ran].)
- `nix develop -c helm version --short` starts with `v3`. (Draft: `v3.20.2` [ran].)
- `flake.lock` is committed and pins `nixos-26.05`.
- Cold realisation time is recorded in the PR. (Draft: 25.1 s wall with warm network [ran], and a truly cold store is about 886 MiB per #1 L60 [read].)
- `.envrc` contains `use flake` and nothing else.
- Verifier: `verify-dev-environment` F1.

**0.2 justfile skeleton** [2] Lane 0, T0.

- `just --list` shows exactly the 14 names.
- Each unimplemented recipe exits 3 and prints `not implemented: owned by #N` with the right N: `up down nuke cluster-verify` are #2, `smoke e2e` are #17, `load-test` is #14, `characterize` is #16.
- Every real recipe calls `scripts/<name>.sh`, no logic lives in the justfile, and `just/` modules are imported by the root file.
- Verifier: F2.

**B.1 Python bootstrap** [4] Lane B, T0.

- `nix develop -c uv lock` exits 0 with an empty `packages/`, and `uv sync --frozen` creates `.venv`. (Draft [ran].)
- `.venv/bin/python --version` equals the flake's `python3 --version` (3.13.15), not a uv-managed Python.
  Regression to add: with a `cpython-3.13.14` present in `~/.local/share/uv/python`, uv picked it unless `UV_PYTHON_PREFERENCE=only-system` was set [ran].
  The criterion fails without that variable.
- `UV_PYTHON` is unset in the shell (asserted, since #1 L59 says it breaks `uv pip`).
- A scratch package with a locked `numpy` imports in the shell.
  On the author's host it imports without the shim [ran], so the shim criterion is "documented, and the check passes both with and without `LD_LIBRARY_PATH` set".
  The shim is verified on a non-NixOS image only if the epic keeps that claim (open).
- Verifier: F4.

**C.1 `just doctor`** [3] Lane C, T0 (self-test) and T1 (live).

- On the author's host: exit 0, printing the version of nix, docker, kernel and the `inotify` limit. (All inputs readable [ran].)
- `path-provenance`: for each of the 14 tools, the first match on PATH is under a flake path and not under `$HOME/.nix-profile` or `/nix/var/nix/profiles`, and `which -a` is printed when it fails.
  Reason: outside the shell `kind`, `kubectl` and `helm` resolve via `~/.nix-profile` and `readlink -f` ends in `/nix/store` [ran], so a plain "under `/nix/store`" check would pass while shadowing.
- Outside the shell: exit 1 with `fail in-dev-shell`.
- The `inotify-instances` threshold is 512.
- Self-test `scripts/doctor.test.sh` injects each fault with fake commands and files (`WDF_DOCTOR_ROOT` points at a fixture tree): no docker, cgroup v1, repo under `/mnt/c`, `inotify` below 512, shadow `helm` first on PATH, flakes disabled, memory below the preset.
  Each makes doctor exit 1 and names exactly that check id, and the healthy fixture exits 0.
- `memory-vs-preset` reads `deploy/presets/<WDF_PRESET>.yaml` if it exists (#2 writes it), else prints `warn memory-vs-preset: no preset file yet` and does not fail.
- Verifier: F3.

**D.1 Lint and format wiring** [5] Lane D, T0.

- `just lint` exits 0 on the repo and runs ruff (check and format --check), yamllint, shellcheck, actionlint, kubeconform (on `deploy/**` if present) and nixfmt (check) on the files each owns.
- `scripts/fixtures/lint-bad/` holds one bad file per linter, and `scripts/lint.sh --expect-fail <file>` exits 0 only if that linter fails on it.
  Lane D's test runs all six.
- Lint config lives in the files lane D owns only, with no `[tool.ruff]` in `pyproject.toml`.
- Verifier: F5.

**D.2 CI workflow and test recipes** [6] Lane D, T0.

- `.github/workflows/ci.yml` has one job whose only run step is `nix develop -c just ci` (checked by `actionlint` and by F6's parse).
- `just test` and `just itest` exit 0 with nothing collected and print the notice.
  With a trivial passing test dropped in a scratch package they exit 0, and with a failing one exit 1 (checked in scratch, not committed).
- No kind smoke job is present (moved to #2's exit).
- Evidence outside the sandbox: one green CI run URL on the PR.
- Verifier: F6.

**E.1 WASM toolchain pins** [7] Lane E, T0.

- `nix develop -c wasmtime --version` prints `48.0.7` and `wasm-tools --version` prints the pinned version.
  The fetch is hash-checked: corrupting the hash in `nix/wasm-tools.nix` makes `nix develop` fail with a hash mismatch.
  (Pins are read from #1 L11 and not run. nixos-26.05 itself carries wasmtime 45.0.2 and wasm-tools 1.249.0 [ran: `nix eval`].)
- `componentize-py` is not pinned here, because `uv.lock` is lane B's and the package is first needed by #11.
  `docs/versions-policy.md` records that #11 adds a hash-pinned entry to `uv.lock` in E7.
  This deviates from #1 detail issue 7 (correction 5 in section 0.1).
- Verifier: F1.

**E.2 `deploy/versions.yaml` and bump policy** [8] Lane E, T0.

- `contracts/versions.schema.json` validates `deploy/versions.yaml` (`just versions --check`, exit 1 on a missing digest).
- `just versions` prints the 14 tool versions from the shell and the file's entries.
- `docs/versions-policy.md` states that a tool bump is a `flake.lock` update, a chart or image bump is one edit, and first-party charts are preferred (#1 L63).
- Verifier: F1.

**V.1 `verify-dev-environment` skill** Lane V, T0 and T1. See section 6.

- The skill is proved end to end on one feature (F1) and its evidence survives cleanup.
- `just verify dev-environment` runs F1 to F7.
- `just verify-lint` passes.

**V.2 `docs/dev-setup.md` and `.gitignore`** Lane V, T0.

- A reader with only the host prerequisites reaches `just doctor` green by following the file, tested by running its commands from a clean export (F7's export).
- The README link resolves.
- `.gitignore` contains `.verify-evidence/` and `.venv/`.

## 5. Order, stubs and spikes

- Barrier: lane 0 (0.1 then 0.2, one worker, merged before anything else).
  The estimate is half a day, since the flake is 12 lines and already evaluates [ran].
- After the barrier: B, C, D, E and V start together, five workers.
  D.2 waits for B.1 only to run pytest, and can write against the stub `scripts/test.sh` meanwhile.
  V.1 starts at the barrier against the stubs and finalises after the others merge, and V.2 is last.
- Stubs: the `scripts/*.sh` stubs from lane 0 are the stubs, and the conformance test is F2 (the recipe table), which runs against stub and real script alike.
- Spikes: none.
- Overlap with #2: the kind smoke job is not built here and is #2's exit acceptance.

## 6. The verifier: `verify-dev-environment`

- Surface: CLI, `nix develop` shell and `just`.
  The harness is bash against a clean export, nothing interactive, no browser, no PTY.
- Tier: T0 for F1 to F6, and T1 only because doctor reads real Docker and the host.
- Launch: create a scratch run directory and export a clean tree into it with `git archive HEAD | tar -x -C $RUN_DIR/tree` (committed state, what CI sees).
  `--dirty` instead copies `git ls-files -co --exclude-standard`.
  Ready means `nix develop $RUN_DIR/tree -c just --version` exits 0.
  No server, no ports, no data dir.
- Doctor (`scripts/verify/dev-environment/doctor.sh`, read-only): nix present with flakes, Docker reachable, run directory owned by the caller, `flake.lock` hash equal to the one recorded at launch, at least 5 GiB free on the store filesystem, and `/tmp/wdf-verify-dev-environment.lock` not held by another run.
- Evidence (`.verify-evidence/dev-environment/<run id>/`): per feature `cmd.txt`, `stdout.txt`, `stderr.txt`, `exit`, plus the side effect that proves it (`which-a.txt`, `drvpath.txt`, `lint-each.txt`), and `summary.json`.
- Cleanup: remove `$RUN_DIR` (scratch only), release the lock, and delete a scratch package created for F4 and F5.
  Never `nix-collect-garbage`, never kill by name, never remove evidence.
- Helpers: `scripts/verify/dev-environment/{run.sh,doctor.sh,export.sh,clean.sh}`, each shown in `SKILL.md` with its invocation, and `just verify dev-environment [Fn]` calls `run.sh`.

Feature map (`features/*.md`, each with the four cursor H2 sections):

| ID | Feature | Drive (observable proof) | Tier | Status at epic close |
| --- | --- | --- | --- | --- |
| F1 shell-pins | The shell resolves every pinned tool from the flake | `nix develop -c bash -c '...'` for 16 tools: first PATH match under the flake, versions equal the lock and `deploy/versions.yaml`, helm major 3, `nix flake check` exit 0, and a corrupt hash in a scratch copy fails `nix develop` | T0 | scripted |
| F2 recipes | Entrypoint contract | `just --list` has exactly the 14 names, each stub exits 3 with the owner text, `just versions` exit 0 | T0 | scripted |
| F3 doctor | Host preflight | healthy: exit 0 with versions. `scripts/doctor.test.sh` injects the seven faults, each exits 1 naming one check. Outside the shell exits 1 with `in-dev-shell` | T0, T1 | scripted |
| F4 python | uv bootstrap | `uv lock && uv sync --frozen` in a scratch workspace, venv Python equals the flake's, a pre-existing uv-managed Python is not selected, scratch `numpy` imports | T0 | scripted |
| F5 lint | Lint wiring | `just lint` clean on the export, and each of six bad fixtures makes its own linter fail | T0 | scripted |
| F6 ci-parity | CI runs the same command | `ci.yml` has one job whose only command is `nix develop -c just ci`, `just ci` exit 0 locally, and if `gh` is usable the latest run on the commit is `success` (else reported as not checked) | T0 | scripted |
| F7 reproducible | Two checkouts give one shell | two exports evaluate `devShells.x86_64-linux.default.drvPath` to the same path, and `docs/dev-setup.md` commands run from the export reach `just doctor` ok | T0 | scripted |

Not yet verifiable, kept in Gotchas and in the status table as `planned`:

- `just up`, `down`, `nuke`, `cluster-verify` (#2)
- `smoke`, `e2e` (#17), `load-test`, `characterize`
- Docker Desktop, non-WSL Linux, macOS
- cold binary-cache behaviour, which needs a clean Nix store that a worker must not create on the shared host

Honest limit: the exit test cannot prove "a second contributor's laptop".
F7 is its stand-in (same derivation path, same lock).
The setup steps a real second machine would do stay unverified until a machine other than the author's host runs the doctor: installing Nix, enabling flakes, installing Docker.

## 7. Exit test

Run by a worker who did not write the lanes, on a clean export of `main`.

| # | Command | Expected | Evidence file |
| --- | --- | --- | --- |
| 1 | `nix flake check` and `nix develop -c just versions` | exit 0, 16 tool versions and the pins file printed, helm major 3, wasmtime 48.0.7 | `F1/` |
| 2 | `just doctor` in the shell, then `scripts/doctor.test.sh` | exit 0 on the host, seven injected faults each exit 1 naming one check | `F3/` |
| 3 | `just doctor` outside the shell (`env -i`) | exit 1, `fail in-dev-shell` | `F3/` |
| 4 | `just lint` and the six bad fixtures | clean, then six distinct failures | `F5/` |
| 5 | `just --list`, then each stub recipe | 14 names, stubs exit 3 with owner | `F2/` |
| 6 | `just test`, `just itest`, `just ci` | exit 0 (nothing to collect, notice printed) | `F6/` |
| 7 | two exports, `nix eval ...drvPath` | identical | `F7/` |
| 8 | `uv sync --frozen`, then Python provenance | `.venv` Python is the flake's, not `~/.local/share/uv/python/...` | `F4/` |

Plus outside the host: one green `check` job run on the PR, with its URL in Closure.
CI was not run while preparing this spec.

## 8. Parallelism and cost

- Maximum parallel workers: 5 (lanes B, C, D, E, V), after a one-worker barrier.
- Host contention: none.
  Nothing here binds a port or starts a cluster.
  Five worktrees running `nix develop` share the store, a cold shell was 25 s [ran], and the first full realisation is about 886 MiB once (#1 L60).
- Critical path, as estimates and not measurements: lane 0 (0.5 d), the longest lane C (1 d, the self-test with fault injection), V finalise and exit test (0.5 d).
  That is about 2 days elapsed and 6 worker-days of effort.

## 9. Closure (filled at close)

Not closed yet.

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).
