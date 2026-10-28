# Dev setup

One Nix flake pins every tool, and `just` is the single entrypoint.
The same commands run on a laptop, on a second contributor's machine and in CI.
This document, `just doctor` and the CI `check` job are the verification surface of the dev environment, which has no verifier skill.
Breakdown: [docs/epics/01-dev-environment.md](epics/01-dev-environment.md)

## Host prerequisites

Only the following live outside the flake.
Everything else is pinned by `flake.lock`.

| Prerequisite | Needed because | Checked by `just doctor` |
| --- | --- | --- |
| Nix with `experimental-features = nix-command flakes` | provides the dev shell | `nix-flakes` |
| Docker engine, major version 24 or newer, daemon reachable | kind runs cluster nodes as containers | `docker-engine`, `docker-version` |
| WSL2 with systemd as PID 1 | the cluster tooling assumes systemd-managed Docker | `wsl-systemd` |
| cgroup v2 (unified hierarchy) | kind nodes require it | `cgroup-v2` |
| Repo on the WSL ext4 filesystem, not under `/mnt/c` | file watching and Nix evaluation over 9p are slow and unreliable | `repo-on-ext4` |
| `fs.inotify.max_user_instances` of at least 512 | many kind nodes and watchers exhaust the default | `inotify-instances` |

The Nix installation, flakes setting and Docker installation are the steps that stay unverified until a machine other than the author's host runs the doctor.

## Entering the shell

```sh
nix develop
```

This realises the 14 flake tools plus `wasmtime` and `wasm-tools`, then sources `scripts/env.sh`.
A one-off command runs without an interactive shell:

```sh
nix develop -c just doctor
```

With direnv installed, `.envrc` (`use flake`, nothing else) enters the shell automatically.
It needs a one-time `direnv allow` in the repo root.
`just` is only available inside `nix develop`, so outside the shell every recipe runs as `nix develop -c just <recipe>`.

A first realisation downloads roughly 886 MiB.
Later entries take seconds.

## Recipes

`just --list` prints exactly 17 names.
Recipes that are not built yet print `not implemented: owned by #N` and exit 3.
No logic lives in the justfile, and each real recipe calls `scripts/<name>.sh`.

| Recipe | Status | Owner | What it does |
| --- | --- | --- | --- |
| `doctor [--json]` | real | - | host preflight, see below |
| `versions [--check]` | real | - | prints tool versions and `deploy/versions.yaml`, `--check` validates the file against its schema |
| `lint` | real | - | ruff, yamllint, shellcheck, actionlint, kubeconform and nixfmt over the files each owns |
| `test` | real | - | `pytest -m "not integration"`, nothing collected counts as success |
| `itest [domain]` | real | - | `pytest -m integration`, optionally limited to `packages/<domain>` |
| `ci` | real | - | `lint`, `verify-lint`, `test`, `itest`, `nix flake check` |
| `verify-lint` | real | - | checks each `verify-<domain>` skill has a `SKILL.md` named after its directory, passes when none exist |
| `verify` | stub | cluster | end-to-end verifier run |
| `verify-clean` | stub | cluster | removes verifier evidence |
| `up` | stub | cluster | create the cluster |
| `down` | stub | cluster | stop the cluster |
| `nuke` | stub | cluster | destroy the cluster and its state |
| `cluster-verify` | stub | cluster | cluster acceptance checks |
| `smoke` | stub | acceptance | end-to-end smoke run |
| `e2e` | stub | acceptance | end-to-end run |
| `load-test` | stub | load | load generation |
| `characterize` | stub | characterization | characterization run |

A stub fails the way a real failure does, so a script cannot mistake it for success:

```text
$ nix develop -c just up
scripts/up.sh
not implemented: owned by #2
error: recipe `up` failed on line 2 with exit code 3
```

## Python with uv

`pyproject.toml` declares a uv workspace with `members = ["packages/*"]` and `requires-python = ">=3.13,<3.14"`.
Creating the environment from the lockfile:

```sh
nix develop -c uv sync --frozen
```

`scripts/env.sh` exports `UV_PYTHON_DOWNLOADS=never` and `UV_PYTHON_PREFERENCE=only-system` and unsets `UV_PYTHON`.
`UV_PYTHON_DOWNLOADS=never` alone does not keep uv on the flake's interpreter.
A uv-managed `cpython` already present in `~/.local/share/uv/python` still wins over the Python on PATH.
`only-system` makes uv choose the flake's Python, so `.venv/bin/python` is the interpreter in `/nix/store` and the same version on every machine.
`UV_PYTHON` stays unset because setting it breaks `uv pip`.

`scripts/env.sh` also adds a private directory with `libstdc++`, `libgcc_s` and `libz` from the host to `LD_LIBRARY_PATH`, so manylinux wheels such as numpy import under the Nix interpreter.
`WDF_LD_EXTRA=<dir>[:<dir>...]` appends further library directories.

Provenance check:

```sh
nix develop -c bash -c '.venv/bin/python --version; readlink -f .venv/bin/python'
```

The resolved path must start with `/nix/store/`.

## `just doctor`

```sh
nix develop -c just doctor
```

Each line is `ok|warn|fail <id>: <detail>`.
The exit code is 0 when no check fails and 1 otherwise, and a warning never fails the run.
`--json` prints `[{id,status,detail}]` instead.
Outside the flake shell the only result is `fail in-dev-shell`, because host copies of tools can shadow the pins.

| Check | Passes when | When it fails |
| --- | --- | --- |
| `in-dev-shell` | `IN_NIX_SHELL` is set | run through `nix develop -c just doctor` or enter the shell first |
| `nix-flakes` | `nix-command` and `flakes` are both enabled | add `experimental-features = nix-command flakes` to `~/.config/nix/nix.conf` or `/etc/nix/nix.conf` |
| `docker-engine` | `docker info` reaches a daemon | start the Docker service, and on WSL2 check that systemd is running and the user can reach the socket |
| `docker-version` | server major version is 24 or newer | upgrade Docker engine |
| `cgroup-v2` | `/sys/fs/cgroup/cgroup.controllers` exists | boot with the unified hierarchy, the WSL2 kernel does this by default and a `cgroup_no_v1=all` or v1 override in `.wslconfig` must be removed |
| `wsl-systemd` | PID 1 is systemd | set `[boot]` `systemd=true` in `/etc/wsl.conf`, then `wsl --shutdown` from Windows |
| `repo-on-ext4` | the repo is not under `/mnt/<drive>/` and not on a 9p, drvfs, cifs or nfs mount | move the clone under the WSL home directory, a `warn` means a non-ext4 local filesystem |
| `inotify-instances` | `fs.inotify.max_user_instances` is at least 512 | `sudo sysctl fs.inotify.max_user_instances=512`, persisted in `/etc/sysctl.d/` |
| `memory-vs-preset` | host memory covers `memory_mib` in `deploy/presets/$WDF_PRESET.yaml` (default preset `ci`) | give WSL2 more memory in `.wslconfig`, a `warn` with `no preset file yet` is expected until cluster presets exist |
| `path-provenance` | for all 14 tools the first match on PATH is a `/nix/store` path, not `~/.nix-profile` or `/nix/var/nix/profiles` | the detail prints `which -a` for each offender, so a profile copy ahead of the flake can be removed or PATH reordered |

`scripts/doctor.test.sh` is the fault-injection self-test.
It builds fixture trees and breaks one condition at a time (no docker, cgroup v1, repo under `/mnt/c`, low inotify, shadowing `helm`, flakes disabled, low memory, outside the shell).
Each fault must fail exactly the matching check, and the healthy tree must pass.

```sh
nix develop -c scripts/doctor.test.sh
```

## Lint

```sh
nix develop -c just lint
```

The recipe runs ruff (check and format), yamllint (`--strict`, 120 columns), shellcheck, actionlint, kubeconform (over `deploy/**`, except the pins file) and nixfmt.
A linter with no files to check prints `skip <name>: no files`.
Lint works on a git checkout or on a `git archive` export, and it exits 1 when no file at all was linted in a tree that has files.
Config lives in `ruff.toml`, `.yamllint`, `.shellcheckrc` and `.editorconfig`, and not in `pyproject.toml`.
`scripts/fixtures/lint-bad/` holds one bad file per linter.
`scripts/lint.sh --self-test` proves each linter rejects its fixture, and `scripts/lint.sh --expect-fail <file>` does the same for one file.

## Version pins

| Pin | File | Bump |
| --- | --- | --- |
| Shell tools | `flake.lock` | `nix flake update`, then review the `just versions` diff |
| `wasmtime`, `wasm-tools` | `nix/wasm-tools.nix` | edit `version`, `url` and `hash` for that tool |
| Python dependencies | `uv.lock` | `uv lock --upgrade-package <name>` |
| kind node image, charts, images | `deploy/versions.yaml` | one edit setting both `version` and `digest` |

`just versions` prints the tool versions from the shell, then the file's entries, one key per line.
`just versions --check` validates `deploy/versions.yaml` against `contracts/versions.schema.json` and exits 1 on a missing digest.
The full policy, including the preference for first-party charts, is in [versions-policy.md](versions-policy.md).

## CI

`.github/workflows/ci.yml` has one job, `check`, that runs on pushes to `main` and on pull requests.
Its steps are checkout, Nix installation, `nix develop -c just ci`, and a reproducibility check.
The reproducibility check runs `git archive HEAD` into two fresh directories and evaluates `.#devShells.x86_64-linux.default.drvPath` in each.
The job fails when the two derivation paths differ, which stands in for a second contributor's machine.

CI can be switched off in two ways:

- Setting the repository variable `CI_ENABLED` to `false` skips the `check` job through the job-level `if`.
  Any other value, or no variable, leaves it on.
- A commit message containing `[skip ci]` skips the runs natively in GitHub Actions.

Locally, `nix develop -c just ci` is the same gate as the CI step.
Lint on the workflow file is part of it, so a line over 120 columns in `ci.yml` fails before the push.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `fail in-dev-shell` | the command ran outside the shell, use `nix develop -c just doctor` |
| `fail path-provenance` naming `kind`, `kubectl` or `helm` | a copy in `~/.nix-profile` is ahead of the flake, remove it with `nix profile remove` or run inside `nix develop` |
| `fail docker-engine` | daemon is down or unreachable, start Docker and confirm `docker info` works outside the shell |
| `fail cgroup-v2` or `fail wsl-systemd` | WSL2 configuration, see the table above, then `wsl --shutdown` |
| `fail repo-on-ext4` | the clone is on a Windows drive, clone again under the WSL home directory |
| `fail inotify-instances` | raise the sysctl as above |
| `warn memory-vs-preset: no preset file yet` | expected until cluster presets exist in `deploy/presets/`, no action |
| `uv sync` picks a different Python | `UV_PYTHON_PREFERENCE` is not `only-system`, which means the shell was not entered through the flake |
| `error: experimental feature 'flakes' is disabled` | Nix config lacks flakes, see `nix-flakes` |

## What green looks like

On a clean checkout, with the host prerequisites in place:

- [ ] `nix develop -c just doctor` exits 0, prints no `fail` line, and prints `ok path-provenance: 14 tools resolve first to the flake`.
  Only `warn memory-vs-preset: no preset file yet` is acceptable until cluster presets exist.
- [ ] `nix develop -c just --list` prints exactly 17 recipe names.
- [ ] `nix develop -c just ci` exits 0, ending with `all checks passed!` from `nix flake check`.
  The tests print `notice: no tests collected` until a package adds some.
- [ ] The reproducibility check prints one `/nix/store/...-nix-shell.drv` path and exits 0.
  The two exports give the same path, otherwise it prints `drvPath differs` and exits 1.
- [ ] The CI `check` job is green on the pull request.
