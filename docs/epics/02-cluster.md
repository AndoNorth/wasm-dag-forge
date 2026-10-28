# Epic #2: Fixed-resource cluster and Tilt - breakdown

Status: draft
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/2
Architecture note: docs/architecture/09-cluster-and-tilt.drawio
Verifier: .claude/skills/verify-cluster/ (created in lane 0)
Depends on (closed epics): Dev environment (#1, closed)

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-10-28) while preparing this spec, `[read]` means read and not re-run.
Epic line references are not used, because the epic issue body was rewritten with the spike sizing results on 2026-10-28 and this spec was written against that version.
Method: [docs/method.md](../method.md).
Spike reports consulted: Temporal at 100,000 jobs (S3b) and the object store (S5), both held outside the repo until their result files land under `docs/spikes/`.

## 0. Decisions for the captain (max 5)

Each question has a recommendation and a default that applies when there is no answer.
The fifth blocks lane 0 and the others do not block any lane start.

| # | Question | Options | Recommendation | Default if no answer |
| --- | --- | --- | --- | --- |
| 1 | Where does the weekend soak run, and which host do the 100,000-job runs use? The epic and the characterization epic leave it open on purpose, and it sets the `soak` preset. | (a) this 7.8 GiB WSL laptop, (b) a larger WSL VM, (c) a dedicated Linux host | Keep it open through this epic. The footprint spike (S2) ends with a host requirement table, one row per preset, and the choice is made from that table before characterization opens. This spec ships `soak` as a schema-valid preset marked `fitted: false`, which `just up` refuses on a host below its `memory_mib`. | `soak` stays unfitted and unstartable, and nothing in this epic depends on it. |
| 2 | Does this epic ship the real infra modules (Temporal, Postgres, RabbitMQ, object store, observability), or only the module contract and a dummy module? The epic's detail issues 7 and 8 say "first modules", while its Does-not-own and Interfaces sections give each module to the owner epic (state and storage, workflow engine, broker, observability). | (a) contract plus dummy modules, real modules by their owners, (b) real modules here | (a). It matches the delivery method, keeps this epic free of component tuning, and lets every owner epic plug into a proven contract. | (a) |
| 3 | What can the footprint spike prove at the exit of this epic, when only dummy modules exist? Real Temporal and Postgres do not exist until their owner epics merge. | (a) host fit with ballast stand-ins sized from the measured figures, real footprints re-measured by characterization, (b) delay the spike until the real modules exist | (a). Host fit is the one finding that can stop the PoC on this machine, so it is answered first and the gate is not held back. The pass line is in section 5. | (a) |
| 4 | Do all presets have the same six node roles (control plane, infra, obs, compile, simulate, aggregate) with one node per role in `ci`, and does `aggregate` get its own node? The epic lists "whether `aggregate` needs its own node or shares one" as open. | (a) same six roles in every preset, `aggregate` has its own node, (b) shared node for `aggregate` and `simulate` in `ci` | (a). A taint is per node, so sharing a node would need a second isolation mechanism that the epic does not define, and "presets change only the numbers" stays true. The cost is one more idle node in `ci` (about 185 MiB measured idle), and S2 tells whether that fits. | (a) |
| 5 | The epic needs changes in files the closed dev environment epic owns: three recipes `verify`, `verify-clean`, `verify-lint` (the method puts `verify-lint` into `just ci`), a kubeconform exclusion for non-manifest YAML under `deploy/` (the lint glob is `deploy/*.yaml`, so `deploy/presets/*.yaml` would be linted as Kubernetes manifests), and the "exactly 14 names" sentence in `docs/dev-setup.md`. Handled as a `revisit` child issue of the dev environment epic, or as a lane 0 edit? | (a) one `revisit` child, blocking lane 0, as the method says for gaps in a closed epic, (b) lane 0 edits the files directly | (a). The method forbids rewriting a closed epic, and the child records the recipe table moving from 14 names to 17 (recipe table v2) with its consumers. Section 2 lists it under Consumes once filed. | (a) |

## 1. Goal and non-goals

Goal: a local Kubernetes cluster with fixed resources, driven by Tilt, so saturation, queueing and backpressure are reproducible.
Capacity is a known number: Kubernetes allocatable equals the preset, and the limits are provably real.

Non-goals for this pass:

- everything the epic lists under "Does not own", including component contents, placement policy, worker process, dashboards, load scenarios, chaos scripts, soak criteria, cloud clusters, autoscaling, NetworkPolicy, PriorityClasses and disk quotas
- the real infra modules, which their owner epics ship against the module contract (decision 2)
- the `dev` and `soak` presets running on this host, which cannot hold them [read], so they are schema-checked and rendered only, with the fit stated by S2
- Docker Desktop and non-WSL Linux hosts, which stay unverified as in the dev environment
- Helm 4, and `resource_deps` on a disabled resource, which the epic marks untested and the design avoids
- chart and image sources for the workflow engine and the broker, which stay decisions of those epics

## 2. Interfaces

### Consumes (each pinned to a version)

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |
| Dev environment | Dev shell with kind, ctlptl, kubectl, helm, tilt, just | `flake.lock` | none, exists [ran: `just --list` prints 14 names] |
| Dev environment | `deploy/versions.yaml`: `kind_node_image` (v1.35.0 with digest), empty `charts` and `images` maps | schema v1 | none, exists [read] |
| Dev environment | `just doctor`, reading top-level `memory_mib:` from `deploy/presets/<preset>.yaml` with a `sed` match on `^memory_mib:` | doctor contract v1 | none, exists [read: `scripts/doctor.sh` line 113] |
| Dev environment | Recipe stubs `up`, `down`, `nuke`, `cluster-verify` exiting 3 with `not implemented: owned by #2` | recipe table v1 | the stubs are what this epic replaces |
| Dev environment | `revisit` child issue for recipe table v2, the lint exclusion and the dev-setup count (decision 5) | filed after approval | none, blocks lane 0 |
| Spike reports | Temporal and its Postgres sizes at 100,000 jobs, object store idle size | S3b section 7, S5 section 4.5 | numbers are carried into section 5 as inputs, not as contracts |

Host facts checked [ran]: 7,942 MiB total memory with 4,953 MiB available while other work shares the Docker daemon, 6 CPUs, `fs.inotify.max_user_instances` 512 (from the dev environment spec).
Host facts from the epic [read]: idle control plane node about 717 MiB, idle worker node about 185 MiB, Temporal peak 686 MiB at a 1 GiB limit, Postgres for Temporal 1 GiB limit and at least 4 GiB of disk (3.5 GiB used), SeaweedFS about 131 MiB idle and 266 MiB after a test run.

### Produces (each pinned precisely)

Shapes below are proposals made concrete in lane 0, and a change after the pin is a contract-change PR.

| To | What | Where it lives (path) | Shape (schema file, signature, exit codes, key names) | Version |
| --- | --- | --- | --- | --- |
| all epics, doctor | Preset schema and the three presets `ci`, `dev`, `soak` | `contracts/preset/`, `deploy/presets/<name>.yaml` | Top-level scalars `name`, `fitted`, `memory_mib` (sum of node memory limits, checked by the loader test, read by doctor), `cpus`. A `nodes` list of `{role, count, cpus, memory_mib}` with `role` in `control-plane, infra, obs, compile, simulate, aggregate`. A `host` block with `ports` and `data_dir`. A `mirror` flag. The kubelet reservation is derived from the node limit by one rule written in the contract README, and is not stored per preset. | preset v1 |
| module owners (state and storage, workflow engine, broker, observability) | Module contract | `contracts/module/`, `deploy/<component>/module.yaml` and `deploy/<component>/Tiltfile.module` | Manifest keys `name`, `group` (`infra`, `control`, `workers`, `obs`), `namespace`, `artifact` (chart or image, pinned in `deploy/versions.yaml`), `resources` (requests and limits per workload), `readiness` (command or resource condition), `profiles` (any of `minimal, dev, full, ci`). The Tilt side loads one `Tiltfile.module` per component and registers one resource group. An `obs` resource is never listed in another group's dependencies. | module v1 |
| placement and workers, observability, load and chaos | Pool and namespace topology | `contracts/topology/` | Node label `wdf.dev/pool=<compile\|simulate\|aggregate\|infra\|obs>` and taint `wdf.dev/pool=<name>:NoSchedule` on pool nodes (the `wdf.dev` prefix is a placeholder). Namespaces `wdf-infra`, `wdf-control`, `wdf-workers`, `wdf-obs`, each with a ResourceQuota and a LimitRange. `wdf-workers` quota is set slightly above pool allocatable. Rule for consumers: detect an OOM by exit code 137 plus node events. | topology v1 |
| all epics | Cluster recipes | `just/cluster.just`, `scripts/up.sh`, `down.sh`, `nuke.sh`, `cluster-verify.sh` | `up` is idempotent and takes `WDF_PRESET` (default `ci`) and exits 0 when the cluster is Ready and verified. `down` stops nodes and keeps data. `nuke` destroys the cluster and the host data directory. `cluster-verify` runs the cluster verifier at tier T2 and is the infrastructure half of the smoke level. Exit codes: 0 ok, 1 check failed, 2 usage, 4 refused (preset needs more memory than the host has, or a cluster this run did not start). | recipe table v2 |
| load and chaos, acceptance | Host access endpoints | `deploy/host/ports.yaml` | Fixed table of `127.0.0.1:<port>` to NodePort, one row per exposed endpoint, usable without Tilt running. Rows exist for the dispatch and control API (one replica in `wdf-control`) and the module endpoints that need host access. | ports v1 |
| characterization | Presets, host requirements and `tilt ci` start | `deploy/presets/`, `docs/cluster-host.md`, `just up` | Per-preset table of nodes, CPU, memory, data directory and the host requirement from S2. | preset v1 |
| compile workloads | Package mirror | `deploy/mirror/` | A wheelhouse module in `wdf-infra` serving hash-pinned wheels, on in `ci`. The consumer installs with `--no-index --find-links` and `--require-hashes`. Wheel contents are produced by the toolchain epic. | module v1 |
| acceptance | Kind smoke CI job | `.github/workflows/kind-smoke.yml` | One job `kind-smoke` running `just up` on `ci`, `just cluster-verify` and `just down`. | v1 |

## 3. Lanes (disjoint file ownership)

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Contracts and skeleton | 1 | `contracts/preset/**`, `contracts/module/**`, `contracts/topology/**`, `.claude/skills/verify-cluster/SKILL.md`, `.claude/skills/verify-cluster/features/README.md`, `scripts/verify/cluster/run.sh`, `scripts/verify/cluster/doctor.sh`, `scripts/verify/cluster/lib/**`, `just/verify.just`, `deploy/dummy/**`, and until replaced the stub `Tiltfile` and `scripts/cluster/*.sh` | 0.1, 0.2 | the `revisit` child on the dev environment epic merged (barrier) |
| A Cluster | 1 | `deploy/presets/*.yaml`, `deploy/cluster/**`, `scripts/cluster/**`, `scripts/up.sh`, `just/cluster.just`, `.claude/skills/verify-cluster/features/{cluster-up,hard-limits,topology}.md`, `scripts/verify/cluster/features/{cluster-up,hard-limits,topology}.sh` | A.1, A.2, A.3, A.4, then A.5 after S2 | lane 0 |
| N Namespaces | 1 | `deploy/namespaces/**`, `scripts/cluster/namespaces.sh` | N.1 | lane 0, and A.4 for presets to read |
| T Tilt | 1 | `Tiltfile`, `deploy/tilt/**`, `scripts/tilt/**`, `.claude/skills/verify-cluster/features/tilt-profiles.md`, `scripts/verify/cluster/features/tilt-profiles.sh` | T.1, T.2 | lane 0 (T.1 needs no cluster) |
| H Host | 1 | `deploy/host/**`, `scripts/down.sh`, `scripts/nuke.sh`, `docs/cluster-host.md`, `.claude/skills/verify-cluster/features/{host-access,persistence}.md`, `scripts/verify/cluster/features/{host-access,persistence}.sh` | H.1, H.2, H.3 | lane 0, and A.2 for a cluster to exist |
| W Mirror | 1 | `deploy/mirror/**`, `.claude/skills/verify-cluster/features/mirror.md`, `scripts/verify/cluster/features/mirror.sh` | W.1 | lane 0, and A.4 for the compile pool |
| F Footprint | 1 | `spikes/S2/**`, `docs/spikes/S2-footprint.md` | F.1 | lanes A to W merged, runs the cluster |
| X Exit | 1 | `.github/workflows/kind-smoke.yml` | X.1 | lanes A to W merged |

The preset schema feature `presets` is lane 0's (`features/presets.md`, `features/presets.sh`) and is the one feature that is `scripted` when lane 0 merges.

Shared-file rules:

- `.claude/skills/verify-cluster/features/README.md` holds the status table, owned by lane 0.
  One row per feature on its own line, and a lane edits only its own row when it flips a feature, as with `deploy/versions.yaml`.
- `deploy/versions.yaml` is shared with every epic.
  One key per line, and a PR touches only its own keys (lane A the node image, lane N none, lane W the wheelhouse image).
- `Tiltfile` is lane 0's stub until lane T replaces it, and it only loads `deploy/<component>/Tiltfile.module` files and `deploy/tilt/**` libraries.
- `deploy/presets/*.yaml` belongs to lane A for the whole epic, including the sizing edit that S2 triggers (A.5), so lane F writes only the result file.
- The renderer in lane A reads `deploy/host/ports.yaml` (lane H) through the shape pinned in `contracts/preset/`, so neither lane edits the other's file.
- `justfile` only imports modules, and `just/verify.just` comes from the `revisit` child and lane 0.
- `uv.lock` is not touched by this epic.
- `scripts/doctor.sh` is not touched.
  Cluster-specific preflight is in `scripts/verify/cluster/doctor.sh`, which keeps the dev environment's doctor contract at v1.

## 4. Issues

Each block follows the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md).
Proposal numbering from the epic is in brackets.
Every acceptance line is a command with an observable result.
Blocks are replaced by `#NNN` links after filing.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 Cluster presets and kind config renderer | A.1 |
| 2 Hard node limits and verification | A.3 |
| 3 ctlptl cluster and registry definition | A.2 |
| 4 Node pools contract | 0.1 (contract), A.4 (counts and sizes) |
| 5 Namespaces, ResourceQuotas and LimitRanges | N.1 |
| 6 Tiltfile skeleton | T.1 |
| 7 Infra module contract and first modules | 0.1, 0.2 (contract and dummies), real modules by owner epics (decision 2) |
| 8 Observability module (profile `full`) | 0.2 (dummy `obs` module), T.1 (profile gating), real content by observability |
| 9 Python service live-update convention and no-live-reload soak mode | T.2 |
| 10 Host access map | H.1 |
| 11 Persistence | H.2 |
| 12 WSL2 host guide and preflight inputs | H.3, with preflight in `scripts/verify/cluster/doctor.sh` (0.2) |
| 13 Footprint measurement and sizing pass | F.1, A.5 |
| 14 Package mirror and compile egress | W.1 |

**0.1 Pinned contracts** [4, 7] Lane 0, tier T0.

- `contracts/preset/`, `contracts/module/` and `contracts/topology/` each hold a schema or table file, valid and invalid vectors, a loader test, a `version` and a `README.md` naming the owner epic.
- `deploy/presets/ci.yaml`, `dev.yaml` and `soak.yaml` validate against the preset schema, with `soak` carrying `fitted: false`.
  `sed -n 's/^memory_mib: *\([0-9][0-9]*\).*/\1/p' deploy/presets/ci.yaml` prints a number, because doctor reads it that way.
- Invalid vectors each fail with a named reason: a missing role, a node memory below the derived reservation, `memory_mib` not equal to the sum of node memory, an unknown role, a module `obs` dependency from another group.
- The loader test fails when any vector flips result, and runs in `just ci` through `just verify-lint` once the `revisit` child is merged.
- Proof: `just verify cluster presets`, valid vectors exit 0 and each invalid one exits 1 with its reason.

**0.2 Verifier skeleton, dummy modules and stubs** [7, 8, 12] Lane 0, T0.

- `.claude/skills/verify-cluster/` has `SKILL.md` with the required sections in the order the method gives, `features/README.md` with the eight-row table (section 6) and one `features/<feature>.md` per feature with the four H2 sections.
  Every row is `planned` except `presets`, and `planned` rows name the lane that flips them.
- `just verify cluster presets` runs the whole loop on the `presets` feature: launch (a clean export is made), doctor, drive, evidence under `.verify-evidence/cluster/<run id>/summary.json`, cleanup, and the evidence is still present after `just verify-clean cluster`, which prints no resource.
- `scripts/verify/cluster/doctor.sh` checks tools present, `/tmp/wdf-cluster.lock` free, scratch directory writable and host prerequisites, and it reads `.wslconfig` for idle timeouts when `/mnt/c` is reachable and otherwise prints `warn wslconfig: not readable`.
  It never edits `scripts/doctor.sh`.
- `deploy/dummy/` holds one dummy module per group (`infra`, `control`, `workers`, `obs`), each a pinned pause-class image with its requests and limits, readiness and group name, passing the module contract.
  The dummy `obs` module is the proof that nothing outside `obs` depends on it.
- Stubs for `scripts/cluster/*.sh` and the `Tiltfile` exit 3 with `not implemented: owned by #2`, and `scripts/verify/cluster/features/<feature>.sh` for each `planned` feature exits 3 the same way.
- Proof: `just verify cluster presets` output and the `just verify-clean cluster` output pasted in the PR.

**A.1 Preset renderer** [1] Lane A, T0. Needs lane 0.

- `scripts/cluster/render.sh <preset>` writes the ctlptl cluster file and one kubelet patch per node from the preset, and is deterministic: two runs give byte-identical output (`diff` exits 0).
- Per-node kubelet flags are rendered per node and not only for the first node, because kind reads a `KubeletConfiguration` patch from the first node only.
  The rendered file for `ci` carries `system-reserved` cpu and `eviction-hard` memory derived from that node's limit by the contract's rule.
- Kind node count, labels and taints in the output equal the preset's `nodes` list.
- Output validates with `kubeconform` where it is a Kubernetes object, and rendering `dev` and `soak` succeeds without starting anything.
- Proof: `just verify cluster cluster-up --render-only`, the T0 half of `cluster-up`.

**A.2 ctlptl cluster and registry** [3] Lane A, T2. Needs A.1.

- `just up` on `ci` creates one kind cluster `wdf` and a local registry through `ctlptl apply`, using the `kind_node_image` version and digest from `deploy/versions.yaml`, and `kubectl --context kind-wdf get nodes` lists every node Ready.
- A second `just up` with nothing changed exits 0 and changes nothing (the idempotence check compares node and registry container ids before and after).
- `just up` refuses with exit 4 when the current kubectl context is not `kind-wdf` and a cluster it did not start exists, and when `WDF_PRESET` needs more memory than the host.
- An image pushed to the local registry is pulled by a pod without `kind load`.
- Proof: `just verify cluster cluster-up`, which flips `cluster-up` to `scripted` at T2.

**A.3 Hard node limits and canaries** [2] Lane A, T2. Needs A.2.

- After `ctlptl apply`, `scripts/cluster/limits.sh` runs `docker update --cpus --memory --memory-swap` on every node container with `memory-swap` equal to `memory`, disables node swap, and sets restart policy `unless-stopped`.
- `kubectl get nodes -o jsonpath` allocatable memory and CPU per node equal the preset's node size minus the derived reservation, to the MiB (the check prints expected and actual).
- Canaries prove the limits are real: a pod that allocates past a node's memory limit ends with exit code 137 and a node `oom_kill` count above zero in `memory.events`, a pod requesting more than allocatable stays Pending with `Insufficient memory`, and a CPU spinner on a limited node is throttled (`cpu.stat` `nr_throttled` above zero).
- Detection is by exit code 137 plus node events and never by `OOMKilled`, which the check asserts is not relied on.
- `memory.events` `max` is read and reported alongside `oom_kill`, because Temporal at its limit stopped serving with `OOMKilled` false and `oom_kill` 0 while `max` kept rising.
- Proof: `just verify cluster hard-limits`, which flips `hard-limits` to `scripted`.

**A.4 Node pools and sizes** [4] Lane A, T2. Needs A.3.

- Pool nodes carry `wdf.dev/pool` and a `NoSchedule` taint as the topology contract says, and counts and sizes per preset come from `deploy/presets/*.yaml`.
- `kubectl get nodes -L wdf.dev/pool` shows one node per role in `ci`.
  A pod with no toleration is never scheduled on a pool node, and a pod with the toleration and node selector is (two scheduling canaries).
- `compile` and `aggregate` pools set one task per pod: the preset states requests equal limits for one pod slot, starting at 1 CPU and 1 GiB per pod, marked as an estimate to be settled by S2 and characterization.
- Proof: `just verify cluster topology`, the pool half of `topology`.

**A.5 Apply the sizing result** [13] Lane A, T0. Needs F.1 merged.

- `deploy/presets/*.yaml` numbers equal the S2 result table, and `just verify cluster presets` still passes the schema and sum checks.
- `docs/cluster-host.md` is not edited here (lane H wrote it), and the S2 result file is linked from the preset README line.
- Proof: `just verify cluster presets` and the `memory-vs-preset` line of `just doctor` for `ci`.

**N.1 Namespaces, quotas and limit ranges** [5] Lane N, T2. Needs lane 0 and A.4.

- `kubectl get ns` lists `wdf-infra`, `wdf-control`, `wdf-workers`, `wdf-obs`, and each has one ResourceQuota and one LimitRange (`kubectl -n <ns> get resourcequota,limitrange`).
- Quotas are rendered from the preset and not copied: the `wdf-workers` quota is slightly above the pool allocatable sum for that preset, and the check prints both numbers.
- A pod without requests or limits in each namespace is admitted with the LimitRange defaults, and a pod above the quota is rejected with the quota in the message.
- Proof: `just verify cluster topology`, the namespace half of `topology`.

**T.1 Tiltfile skeleton and profiles** [6, 8] Lane T, T0. Needs lane 0.

- The `Tiltfile` carries a context guard that refuses any context other than `kind-wdf`, tested against a second `kind-*` context name (the guard exits non-zero, because Tilt treats every `kind-*` context as local).
- Profiles `minimal`, `dev`, `full` and `ci` are selected through `config.define_string` and `config.set_enabled_resources`.
  `tilt alpha tiltfile-result` offline lists the enabled resource groups per profile against a table in `deploy/tilt/profiles.yaml`: `minimal` infra, `dev` infra, control and workers, `full` dev plus the full obs tier, `ci` dev plus the lean obs tier and the mirror.
  The test runs without a cluster, because the alpha API is the offline selection check.
- The module loader reads each `deploy/<component>/Tiltfile.module` and a module that fails the module contract fails the load with the contract's reason.
- `tilt ci` is wired for `ci` and soak starts, and `just up` calls it for the `ci` preset.
- Proof: `just verify cluster tilt-profiles`, which flips `tilt-profiles` to `scripted` at T0.

**T.2 Live-update convention and the immutable soak mode** [9] Lane T, T0. Needs T.1.

- `deploy/tilt/live-update.md` (one page) states the convention for Python services, and a library function in `deploy/tilt/` applies it to a module that opts in.
- A `soak` start (`tilt ci` with the profile that disables live update) deploys immutable image tags, and the offline profile test asserts that no resource in that mode has a live-update step.
- Live update is only for iteration, and the document says why (replaying open Temporal workflows against live-reloaded code risks non-determinism).
- Proof: the offline profile test of T.1 extended with the immutable-mode assertion.

**H.1 Host access map** [10] Lane H, T2. Needs A.2.

- `deploy/host/ports.yaml` lists each endpoint with a `127.0.0.1:<port>` and a NodePort, and the renderer maps them through `extraPortMappings` bound to 127.0.0.1 only.
- `ss -ltn` on the host shows every mapped port bound to `127.0.0.1` and none to `0.0.0.0`.
- The dummy module's endpoint answers on its host port with Tilt not running (the check stops Tilt first and then curls).
- Proof: `just verify cluster host-access`, which flips `host-access` to `scripted`.

**H.2 Persistence** [11] Lane H, T2. Needs H.1.

- `extraMounts` mounts one host data directory on the infra node, taken from the preset's `host.data_dir`.
- A marker file written through a dummy StatefulSet's PVC survives `just down` followed by `just up`.
- `just nuke` removes the cluster, the registry and the data directory, and a second `just nuke` exits 0 with nothing left (`docker ps -a` and the directory listing show none).
- `just nuke` refuses with exit 4 on a data directory outside the repo's configured path.
- Proof: `just verify cluster persistence`, which flips `persistence` to `scripted`.

**H.3 WSL2 host guide** [12] Lane H, T0. Needs lane 0.

- `docs/cluster-host.md` covers `.wslconfig` memory and processors, `vmIdleTimeout` and `instanceIdleTimeout`, inotify, ext4 location and sleep settings, one sentence per line, with a table of what each setting protects and what no setting covers (Windows sleep and update reboots).
- Every check named in the guide exists in `scripts/verify/cluster/doctor.sh` or in `just doctor`, and the guide links to `docs/dev-setup.md` and does not copy it.
- Proof: `grep` of each named check id against the two scripts, run in the PR.

**W.1 Package mirror** [14] Lane W, T2. Needs lane 0 and A.4.

- `deploy/mirror/` is a module (passing the module contract) serving a wheelhouse directory from `wdf-infra`, with a fixture wheelhouse of one hash-pinned wheel.
- From a pod on the `compile` pool, `pip download --no-index --find-links http://<mirror service> --require-hashes` of the fixture succeeds, and with the hash altered it fails.
- The module is enabled by the `ci` profile and disabled by `minimal`.
- Aggregate pods run with no package egress configured, and no module in this epic gives them any.
- Contents beyond the fixture come from the toolchain epic, and a mirror shape change is a contract-change PR.
- Proof: `just verify cluster mirror`, which flips `mirror` to `scripted`.

**F.1 Footprint spike S2** [13] Lane F, T2. Needs lanes A, N, T, H and W merged.

- Timeboxed to one day, one issue labelled `spike`, throwaway code in `spikes/S2/`, result in `docs/spikes/S2-footprint.md` stating pass or fail, the numbers and the decision it settles.
- Pass line and method are in section 5.
- Proof: the result file with the run's raw output, and the host requirement table.

**X.1 Kind smoke CI job** Lane X, T2. Needs lanes A, N, T, H and W merged.

- `.github/workflows/kind-smoke.yml` has one job `kind-smoke` on `ubuntu-latest` that installs Nix, runs `nix develop -c just up`, `nix develop -c just cluster-verify` and `nix develop -c just down` on the `ci` preset, passes `actionlint`, and does not edit `.github/workflows/ci.yml`.
- The job honours the repository variable `CI_ENABLED` as the `check` job does, and a failed run uploads the cluster logs as an artifact.
- One green run URL on a pull request and one on `main` are linked in the PR and in Closure.
- Proof: the CI run URL, and the runner's memory and CPU printed in the log, recorded because the host size differs from this laptop.

## 5. Order, stubs and spikes

- Barrier: the `revisit` child issue on the dev environment epic (decision 5), then lane 0 (0.1, then 0.2, one worker, merged alone).
  The estimate is one day and a half.
- After lane 0: A.1, T.1 and H.3 start together, and none of them needs a cluster.
  A.2 gates every issue that runs a cluster (A.3, H.1, H.2).
- Cluster-using lanes share one cluster, so they take `flock /tmp/wdf-cluster.lock` and run one at a time on this host.
  Each recipe that touches the cluster takes the lock and a verifier refuses a cluster it did not start.
- Then lanes N, W and the rest of H, then F.1, then A.5, then X.1, then the exit test.
- DAG: lane 0, then (A.1 ‖ T.1 ‖ H.3), then A.2, then (A.3 ‖ H.1), then A.4 and H.2, then (N.1 ‖ W.1 ‖ T.2), then F.1, then A.5 and X.1, then exit.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| `scripts/cluster/*.sh`, `Tiltfile`, per-feature scripts | exit 3 with `not implemented: owned by #2` | the recipe check (`just --list`, each stub exits 3 with owner text) runs against the stub and the real script alike, where the real one must exit 0 or 1 and never 3 |
| `deploy/dummy/*` modules | a pinned pause-class workload per group with requests, limits and readiness | the module contract loader runs against dummy and real modules, so a real module that breaks the shape fails the same vectors |
| ballast stand-ins in `spikes/S2/` | a pod holding its component's measured peak memory and CPU | S2's own raw output; real components are re-measured by characterization, and that comparison is the conformance check |

Spike S2, footprint, gate G1:

- Question: does the `ci` preset fit on this 7.8 GiB host with its nodes at their limits, and what host does each preset need?
- Method: `just up` on `ci` with every module group enabled in the `ci` profile, infra and pool pods replaced by ballast stand-ins sized from the figures in section 2 (Temporal 1 CPU and 1 GiB, Temporal's Postgres 1 GiB, object store at its S5 idle and post-test figures, one compile pod and one aggregate pod at 1 CPU and 1 GiB), held for 15 minutes with host `MemAvailable` sampled every 15 s.
  The run aborts on host `MemAvailable` below 1 GiB, the floor spike S3b used.
- Pass line, binary: the cluster reaches Ready with every node Ready and every ballast pod Running, host `MemAvailable` never falls below 1 GiB, no node container shows `oom_kill` above zero or `memory.events` `max` rising on a node, and `just cluster-verify` exits 0 under load.
  Anything else is fail, and the result file names the smallest `ci` numbers that pass, or says that this host cannot hold `ci`.
- Output: a host requirement table, one row per preset (`ci`, `dev`, `soak`), with the memory and CPU sum, the idle node overhead measured, and the headroom a shared Docker daemon needs.
  `dev` and `soak` are computed from preset numbers and are not started on this host.
- What it blocks: gate G1.
  If S2 fails, the next epic is not opened, and epics that need no cluster continue while the host decision (decision 1) is made.
  Lanes A to W are not gated by S2, and only A.5 and the exit test wait for it.

## 6. Verifier delta

`verify-cluster` is created in lane 0 at tier T2 with T0 features included, and it is the domain verifier named in the method.
Reason: the cluster is a surface driven end to end (a worker runs `just up`, probes nodes and pods, and tears down), so the fixture checks of the dev environment would not prove it.

| Feature | Tier | Epic | Status at lane 0 | Flipped by |
| --- | --- | --- | --- | --- |
| `presets` | T0 | this | scripted | lane 0 |
| `tilt-profiles` | T0 | this | planned: needs lane T | T.1, T.2 |
| `cluster-up` | T2 | this | planned: needs lane A | A.1 (render half), A.2 |
| `hard-limits` | T2 | this | planned: needs lane A | A.3 |
| `topology` | T2 | this | planned: needs lanes A and N | A.4, N.1 |
| `host-access` | T2 | this | planned: needs lane H | H.1 |
| `persistence` | T2 | this | planned: needs lane H | H.2 |
| `mirror` | T2 | this | planned: needs lane W | W.1 |

- New helper scripts: `scripts/verify/cluster/run.sh`, `doctor.sh` and `lib/` (lock, evidence, label and cleanup helpers), plus one script per feature.
- `just verify`, `verify-clean` and `verify-lint` come from `just/verify.just`, with `verify-lint` added to `just ci` by the `revisit` child.
- `just cluster-verify` runs `just verify cluster` at tier T2 and is the infrastructure half of the smoke level, so there is one script and not two.
- Staying `planned` after this epic: nothing from the epic's feature list.
  The behaviour of real Temporal, Postgres, RabbitMQ and the object store inside the pools stays with their owner epics' verifiers, and the footprint of real components stays with characterization.
- Cleanup removes only resources carrying this run's label `wdf.dev/verify-run=<run id>` or a recorded PID, and never removes evidence.
  `just nuke` is the owner's destructive recipe and is not what cleanup calls.

## 7. Exit test

Run by a worker who did not write the lanes, on a clean export of `main`, on this host with the cluster free.
The command output is kept in the PR that records the run, and the verifier evidence at `.verify-evidence/cluster/<run id>/` is summarised in Closure.

| # | Command | Expected | Evidence |
| --- | --- | --- | --- |
| 1 | `nix develop -c just doctor` and `nix develop -c scripts/verify/cluster/doctor.sh` | exit 0, `memory-vs-preset` for `ci` reads `ok` | PR output |
| 2 | `nix develop -c just verify cluster presets` and the `dev` and `soak` render check | schema and sum checks pass, `dev` and `soak` render without starting | `summary.json` |
| 3 | `nix develop -c just up` on `ci`, then again | Ready, then no change on the second run | `summary.json`, node ids |
| 4 | `nix develop -c just verify cluster hard-limits` | allocatable equals preset minus reservation per node, exit 137 and `oom_kill` above zero for the memory canary, `Insufficient memory` for the over-request, throttle above zero | `summary.json`, canary logs |
| 5 | `nix develop -c just verify cluster topology` | pool labels and taints, scheduling canaries, four namespaces with quota and limit range, defaults admitted, over-quota rejected | `summary.json` |
| 6 | `nix develop -c just verify cluster tilt-profiles` | four profiles list the groups in the table, context guard refuses a second `kind-*` context, soak mode has no live update | `summary.json` |
| 7 | `nix develop -c just verify cluster host-access` | mapped ports bound to 127.0.0.1 only, dummy endpoint answers with Tilt stopped | `summary.json`, `ss` output |
| 8 | `nix develop -c just verify cluster persistence` | marker survives `just down` then `just up`, `just nuke` leaves nothing and repeats cleanly | `summary.json` |
| 9 | `nix develop -c just verify cluster mirror` | fixture wheel installs from the mirror from a compile pod, altered hash fails | `summary.json` |
| 10 | `nix develop -c just cluster-verify` | exit 0, all eight features passed | `summary.json` |
| 11 | `nix develop -c just verify-clean cluster` | no resource with the run label, evidence directory exists | command output |
| 12 | `nix develop -c just ci` | exit 0, `verify-lint` included | CI `check` run URL |
| 13 | the `kind-smoke` job on `main` | one green run, the exit acceptance of this epic | run URL in Closure |
| 14 | S2 result file merged with pass or fail stated | host requirement table present, gate G1 decision recorded | `docs/spikes/S2-footprint.md` |

Rows 1 to 12 and 14 run on this host.
Row 13 runs on a GitHub runner, so it adds a second host size to the evidence, and it is a different run from the local one.
Not provable by this epic: the `dev` and `soak` presets starting on this host, real component footprints, Docker Desktop and non-WSL Linux.

## 8. Parallelism and cost

- Maximum parallel workers: 3 after the barrier (A.1, T.1 and H.3), then up to 4 on work that needs no cluster, with cluster work one at a time.
  The cluster is single-owner on this host, so every cluster-using issue takes `/tmp/wdf-cluster.lock` and runs one at a time, which caps real parallelism for lanes A, N, H, W, F and X.
- Host constraints: the `ci` cluster of six nodes on a 7.8 GiB host that already runs other work (4.9 GiB available when checked [ran]), so cluster runs wait for a quiet host, and a worker never starts `dev` or `soak`.
- Estimates, not measurements: lane 0 (1.5 d), lane A (3 d, the longest, A.3 canaries), lanes T, N, H and W (1 to 2 d each, mostly parallel), S2 (1 d timebox), exit lane X and the exit test (1 d).
  That is about 8 days elapsed and 14 worker-days of effort, with the cluster lock as the main source of slip.

## 9. Closure (filled at close)

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).
