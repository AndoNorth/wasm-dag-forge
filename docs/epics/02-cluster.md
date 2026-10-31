# Epic #2: Fixed-resource cluster and Tilt - breakdown

Status: approved
Epic: https://github.com/AndoNorth/wasm-dag-forge/issues/2
Architecture note: docs/architecture/09-cluster-and-tilt.drawio
Depends on (closed epics): Dev environment (#1, closed)

Evidence marks: `[ran]` means executed on the author's host (WSL2, 2026-10-28) while preparing this spec, `[read]` means read and not re-run.
Epic line references are not used, because the epic issue body was rewritten with the spike sizing results on 2026-10-28 and this spec was written against that version.
Method: [docs/method.md](../method.md).
Spike reports consulted: Temporal at 100,000 jobs (S3b) and the object store (S5), both held outside the repo until their result files land under `docs/spikes/`.

## 0. Decisions for the captain (max 5)

Each question has a recommendation and a default that applies when there is no answer.
The fifth blocks lane 0 and the others do not block any lane start.
All five were answered on 2026-10-28 by taking the recommendation.

| # | Question | Options | Recommendation | Default if no answer | Answer |
| --- | --- | --- | --- | --- | --- |
| 1 | Where does the weekend soak run, and which host do the 100,000-job runs use? The epic and the characterization epic leave it open on purpose, and it sets the `soak` preset. | (a) this 7.8 GiB WSL laptop, (b) a larger WSL VM, (c) a dedicated Linux host | Keep it open through this epic. The footprint spike (S2) ends with a host requirement table, one row per preset, and the choice is made from that table before characterization opens. This spec ships `soak` as a schema-valid preset marked `fitted: false`, which `just up` refuses on a host below its `memory_mib`. | `soak` stays unfitted and unstartable, and nothing in this epic depends on it. | Answer: recommendation taken, 2026-10-28 |
| 2 | Does this epic ship the real infra modules (Temporal, Postgres, RabbitMQ, object store, observability), or only the module contract and a dummy module? The epic's detail issues 7 and 8 say "first modules", while its Does-not-own and Interfaces sections give each module to the owner epic (state and storage, workflow engine, broker, observability). | (a) contract plus dummy modules, real modules by their owners, (b) real modules here | (a). It matches the delivery method, keeps this epic free of component tuning, and lets every owner epic plug into a proven contract. | (a) | Answer: recommendation taken, 2026-10-28 |
| 3 | What can the footprint spike prove at the exit of this epic, when only dummy modules exist? Real Temporal and Postgres do not exist until their owner epics merge. | (a) host fit with ballast stand-ins sized from the measured figures, real footprints re-measured by characterization, (b) delay the spike until the real modules exist | (a). Host fit is the one finding that can stop the PoC on this machine, so it is answered first and the gate is not held back. The pass line is in section 5. | (a) | Answer: recommendation taken, 2026-10-28 |
| 4 | Do all presets have the same six node roles (control plane, infra, obs, compile, simulate, aggregate) with one node per role in `ci`, and does `aggregate` get its own node? The epic lists "whether `aggregate` needs its own node or shares one" as open. | (a) same six roles in every preset, `aggregate` has its own node, (b) shared node for `aggregate` and `simulate` in `ci` | (a). A taint is per node, so sharing a node would need a second isolation mechanism that the epic does not define, and "presets change only the numbers" stays true. The cost is one more idle node in `ci` (about 185 MiB measured idle), and S2 tells whether that fits. | (a) | Answer: recommendation taken, 2026-10-28 |
| 5 | The epic needs changes in files the closed dev environment epic owns: three recipes `verify`, `verify-clean`, `verify-lint` (the method puts `verify-lint` into `just ci`), a kubeconform exclusion for non-manifest YAML under `deploy/` (the lint glob is `deploy/*.yaml`, so `deploy/presets/*.yaml` would be linted as Kubernetes manifests), and the "exactly 14 names" sentence in `docs/dev-setup.md`. Handled as a `revisit` child issue of the dev environment epic, or as a lane 0 edit? | (a) one `revisit` child, blocking lane 0, as the method says for gaps in a closed epic, (b) lane 0 edits the files directly | (a). The method forbids rewriting a closed epic, and the child records the recipe table moving from 14 names to 17 (recipe table v2) with its consumers. Section 2 lists it under Consumes once filed. | (a) | Answer: recommendation taken, 2026-10-28 |

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
| Dev environment | Recipe stubs `up`, `down`, `nuke` exiting 3 with `not implemented: owned by #2` | recipe table v1 | the stubs are what this epic replaces |
| Dev environment | `revisit` child issue [#49](https://github.com/AndoNorth/wasm-dag-forge/issues/49) for recipe table v2, the lint exclusion and the dev-setup count (decision 5) | recipe table v2 | none, blocks lane 0 |
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
| all epics | Cluster recipes | `just/cluster.just`, `scripts/up.sh`, `down.sh`, `nuke.sh` | `up` is idempotent and takes `WDF_PRESET` (default `ci`) and exits 0 when the cluster is Ready. `down` stops nodes and keeps data. `nuke` destroys the cluster and the host data directory. Exit codes: 0 ok, 1 check failed, 2 usage, 4 refused (preset needs more memory than the host has, or a cluster this run did not start). | recipe table v2 |
| load and chaos, acceptance | Host access endpoints | `deploy/host/ports.yaml` | Fixed table of `127.0.0.1:<port>` to NodePort, one row per exposed endpoint, usable without Tilt running. Rows exist for the dispatch and control API (one replica in `wdf-control`) and the module endpoints that need host access. | ports v1 |
| characterization | Presets, host requirements and `tilt ci` start | `deploy/presets/`, `docs/cluster-host.md`, `just up` | Per-preset table of nodes, CPU, memory, data directory and the host requirement from S2. | preset v1 |
| compile workloads | Package mirror | `deploy/mirror/` | A wheelhouse module in `wdf-infra` serving hash-pinned wheels, on in `ci`. The consumer installs with `--no-index --find-links` and `--require-hashes`. Wheel contents are produced by the toolchain epic. | module v1 |
| acceptance | Kind smoke CI job | `.github/workflows/kind-smoke.yml` | One job `kind-smoke` running `just up` on `ci` and `just down`. | v1 |

## 3. Lanes (disjoint file ownership)

| Lane | Workers | Owns (exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |
| 0 Contracts and skeleton | 1 | `contracts/preset/**`, `contracts/module/**`, `contracts/topology/**`, `deploy/dummy/**`, and until replaced the stub `Tiltfile` and `scripts/cluster/*.sh` | [#50](https://github.com/AndoNorth/wasm-dag-forge/issues/50), [#51](https://github.com/AndoNorth/wasm-dag-forge/issues/51) | the `revisit` child [#49](https://github.com/AndoNorth/wasm-dag-forge/issues/49) on the dev environment epic merged (barrier) |
| A Cluster | 1 | `deploy/presets/*.yaml`, `deploy/cluster/**`, `scripts/cluster/**`, `scripts/up.sh`, `just/cluster.just` | [#52](https://github.com/AndoNorth/wasm-dag-forge/issues/52), [#55](https://github.com/AndoNorth/wasm-dag-forge/issues/55), [#56](https://github.com/AndoNorth/wasm-dag-forge/issues/56), [#58](https://github.com/AndoNorth/wasm-dag-forge/issues/58), then [#64](https://github.com/AndoNorth/wasm-dag-forge/issues/64) after S2 | lane 0 |
| N Namespaces | 1 | `deploy/namespaces/**`, `scripts/cluster/namespaces.sh` | [#60](https://github.com/AndoNorth/wasm-dag-forge/issues/60) | lane 0, and A.4 for presets to read |
| T Tilt | 1 | `Tiltfile`, `deploy/tilt/**`, `scripts/tilt/**` | [#53](https://github.com/AndoNorth/wasm-dag-forge/issues/53), [#62](https://github.com/AndoNorth/wasm-dag-forge/issues/62) | lane 0 (T.1 needs no cluster) |
| H Host | 1 | `deploy/host/**`, `scripts/down.sh`, `scripts/nuke.sh`, `docs/cluster-host.md` | [#57](https://github.com/AndoNorth/wasm-dag-forge/issues/57), [#59](https://github.com/AndoNorth/wasm-dag-forge/issues/59), [#54](https://github.com/AndoNorth/wasm-dag-forge/issues/54) | lane 0, and A.2 for a cluster to exist |
| W Mirror | 1 | `deploy/mirror/**` | [#61](https://github.com/AndoNorth/wasm-dag-forge/issues/61) | lane 0, and A.4 for the compile pool |
| F Footprint | 1 | `spikes/S2/**`, `docs/spikes/S2-footprint.md` | [#63](https://github.com/AndoNorth/wasm-dag-forge/issues/63) | lanes A to W merged, runs the cluster |
| X Exit | 1 | `.github/workflows/kind-smoke.yml` | [#65](https://github.com/AndoNorth/wasm-dag-forge/issues/65) | lanes A to W merged |

Shared-file rules:

- `deploy/versions.yaml` is shared with every epic.
  One key per line, and a PR touches only its own keys (lane A the node image, lane N none, lane W the wheelhouse image).
- `Tiltfile` is lane 0's stub until lane T replaces it, and it only loads `deploy/<component>/Tiltfile.module` files and `deploy/tilt/**` libraries.
- `deploy/presets/*.yaml` belongs to lane A for the whole epic, including the sizing edit that S2 triggers (A.5), so lane F writes only the result file.
- The renderer in lane A reads `deploy/host/ports.yaml` (lane H) through the shape pinned in `contracts/preset/`, so neither lane edits the other's file.
- `justfile` only imports modules.
- `uv.lock` is not touched by this epic.
- `scripts/doctor.sh` is not touched.
  That keeps the dev environment's doctor contract at v1.

## 4. Issues

The `revisit` child on the dev environment epic is [#49](https://github.com/AndoNorth/wasm-dag-forge/issues/49), and it blocks lane 0.
Each lane issue below was filed on 2026-10-28 from the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md), as a sub-issue of #2, and its body holds the scope, owns, acceptance criteria and needs.
Proposal numbering from the epic is in brackets.
Every acceptance line is a command with an observable result.

### Mapping from the epic's detail issues

| Epic detail issue | Delivered by |
| --- | --- |
| 1 Cluster presets and kind config renderer | A.1 |
| 2 Hard node limits and canaries | A.3 |
| 3 ctlptl cluster and registry definition | A.2 |
| 4 Node pools contract | 0.1 (contract), A.4 (counts and sizes) |
| 5 Namespaces, ResourceQuotas and LimitRanges | N.1 |
| 6 Tiltfile skeleton | T.1 |
| 7 Infra module contract and first modules | 0.1, 0.2 (contract and dummies), real modules by owner epics (decision 2) |
| 8 Observability module (profile `full`) | 0.2 (dummy `obs` module), T.1 (profile gating), real content by observability |
| 9 Python service live-update convention and no-live-reload soak mode | T.2 |
| 10 Host access map | H.1 |
| 11 Persistence | H.2 |
| 12 WSL2 host guide and preflight inputs | H.3, with the checks in `just doctor` |
| 13 Footprint measurement and sizing pass | F.1, A.5 |
| 14 Package mirror and compile egress | W.1 |

- [#50](https://github.com/AndoNorth/wasm-dag-forge/issues/50) **0.1 Pinned contracts** [4, 7] Lane 0, tier T0.
- [#51](https://github.com/AndoNorth/wasm-dag-forge/issues/51) **0.2 Dummy modules and stubs** [7, 8, 12] Lane 0, T0.
- [#52](https://github.com/AndoNorth/wasm-dag-forge/issues/52) **A.1 Preset renderer** [1] Lane A, T0. Needs lane 0.
- [#55](https://github.com/AndoNorth/wasm-dag-forge/issues/55) **A.2 ctlptl cluster and registry** [3] Lane A, T2. Needs A.1.
- [#56](https://github.com/AndoNorth/wasm-dag-forge/issues/56) **A.3 Hard node limits and canaries** [2] Lane A, T2. Needs A.2.
- [#58](https://github.com/AndoNorth/wasm-dag-forge/issues/58) **A.4 Node pools and sizes** [4] Lane A, T2. Needs A.3.
- [#64](https://github.com/AndoNorth/wasm-dag-forge/issues/64) **A.5 Apply the sizing result** [13] Lane A, T0. Needs F.1 merged.
- [#60](https://github.com/AndoNorth/wasm-dag-forge/issues/60) **N.1 Namespaces, quotas and limit ranges** [5] Lane N, T2. Needs lane 0 and A.4.
- [#53](https://github.com/AndoNorth/wasm-dag-forge/issues/53) **T.1 Tiltfile skeleton and profiles** [6, 8] Lane T, T0. Needs lane 0.
- [#62](https://github.com/AndoNorth/wasm-dag-forge/issues/62) **T.2 Live-update convention and the immutable soak mode** [9] Lane T, T0. Needs T.1.
- [#57](https://github.com/AndoNorth/wasm-dag-forge/issues/57) **H.1 Host access map** [10] Lane H, T2. Needs A.2.
- [#59](https://github.com/AndoNorth/wasm-dag-forge/issues/59) **H.2 Persistence** [11] Lane H, T2. Needs H.1.
- [#54](https://github.com/AndoNorth/wasm-dag-forge/issues/54) **H.3 WSL2 host guide** [12] Lane H, T0. Needs lane 0.
- [#61](https://github.com/AndoNorth/wasm-dag-forge/issues/61) **W.1 Package mirror** [14] Lane W, T2. Needs lane 0 and A.4.
- [#63](https://github.com/AndoNorth/wasm-dag-forge/issues/63) **F.1 Footprint spike S2** [13] Lane F, T2. Needs lanes A, N, T, H and W merged.
- [#65](https://github.com/AndoNorth/wasm-dag-forge/issues/65) **X.1 Kind smoke CI job** Lane X, T2. Needs lanes A, N, T, H and W merged.

## 5. Order, stubs and spikes

- Barrier: the `revisit` child issue [#49](https://github.com/AndoNorth/wasm-dag-forge/issues/49) on the dev environment epic (decision 5), then lane 0 (0.1, then 0.2, one worker, merged alone).
  The estimate is one day and a half.
- After lane 0: A.1, T.1 and H.3 start together, and none of them needs a cluster.
  A.2 gates every issue that runs a cluster (A.3, H.1, H.2).
- Cluster-using lanes share one cluster, so they take `flock /tmp/wdf-cluster.lock` and run one at a time on this host.
  Each recipe that touches the cluster takes the lock and `just up` refuses a cluster this run did not start.
- Then lanes N, W and the rest of H, then F.1, then A.5, then X.1, then the exit test.
- DAG: lane 0, then (A.1 ‖ T.1 ‖ H.3), then A.2, then (A.3 ‖ H.1), then A.4 and H.2, then (N.1 ‖ W.1 ‖ T.2), then F.1, then A.5 and X.1, then exit.

Stubs:

| Stub | What it fakes | How its conformance test runs against the real thing |
| --- | --- | --- |
| `scripts/cluster/*.sh`, `Tiltfile` | exit 3 with `not implemented: owned by #2` | the recipe check (`just --list`, each stub exits 3 with owner text) runs against the stub and the real script alike, where the real one must exit 0 or 1 and never 3 |
| `deploy/dummy/*` modules | a pinned pause-class workload per group with requests, limits and readiness | the module contract loader runs against dummy and real modules, so a real module that breaks the shape fails the same vectors |
| ballast stand-ins in `spikes/S2/` | a pod holding its component's measured peak memory and CPU | S2's own raw output; real components are re-measured by characterization, and that comparison is the conformance check |

Spike S2, footprint, gate G1:

- Question: does the `ci` preset fit on this 7.8 GiB host with its nodes at their limits, and what host does each preset need?
- Method: `just up` on `ci` with every module group enabled in the `ci` profile, infra and pool pods replaced by ballast stand-ins sized from the figures in section 2 (Temporal 1 CPU and 1 GiB, Temporal's Postgres 1 GiB, object store at its S5 idle and post-test figures, one compile pod and one aggregate pod at 1 CPU and 1 GiB), held for 15 minutes with host `MemAvailable` sampled every 15 s.
  The run aborts on host `MemAvailable` below 1 GiB, the floor spike S3b used.
- Pass line, binary: the cluster reaches Ready with every node Ready and every ballast pod Running, host `MemAvailable` never falls below 1 GiB, no node container shows `oom_kill` above zero or `memory.events` `max` rising on a node, and `just up` exits 0 again under load.
  Anything else is fail, and the result file names the smallest `ci` numbers that pass, or says that this host cannot hold `ci`.
- Output: a host requirement table, one row per preset (`ci`, `dev`, `soak`), with the memory and CPU sum, the idle node overhead measured, and the headroom a shared Docker daemon needs.
  `dev` and `soak` are computed from preset numbers and are not started on this host.
- What it blocks: gate G1.
  If S2 fails, the next epic is not opened, and epics that need no cluster continue while the host decision (decision 1) is made.
  Lanes A to W are not gated by S2, and only A.5 and the exit test wait for it.

## 6. Exit test

Run by a worker who did not write the lanes, on a clean export of `main`, on this host with the cluster free.
Each command runs as written, and its real output is pasted in the PR that records the run and summarised in Closure.

| # | Command | Expected |
| --- | --- | --- |
| 1 | `nix develop -c just doctor` | exit 0, `memory-vs-preset` for `ci` reads `ok` |
| 2 | `nix develop -c scripts/cluster/render.sh ci`, `dev` and `soak`, each run twice | all three render without starting anything, the two runs of each differ by nothing under `diff` |
| 3 | `nix develop -c just up` on `ci`, then again | Ready, then no change on the second run |
| 4 | `nix develop -c scripts/cluster/allocatable.sh "$(scripts/cluster/render.sh ci)/limits.yaml"` and `nix develop -c scripts/cluster/canary.sh all` | allocatable equals preset minus reservation per node, exit 137 and `oom_kill` above zero for the memory canary, `Insufficient memory` for the over-request, throttle above zero |
| 5 | `kubectl get nodes -L wdf.dev/pool`, `kubectl get resourcequota,limitrange -A` and a pod that exceeds its namespace quota | pool labels and taints as the preset says, four namespaces with quota and limit range, defaults admitted, over-quota rejected |
| 6 | `tilt alpha tiltfile-result` for each of the four profiles, then again with a second `kind-*` context present | each profile lists the groups in the lane T table, the context guard refuses the second context, soak mode has no live update |
| 7 | `ss -ltn` and a `curl` to the dummy endpoint with Tilt stopped | mapped ports bound to 127.0.0.1 only, the endpoint answers |
| 8 | write a marker, `just down`, `just up`, read the marker, then `just nuke` twice | marker survives, `just nuke` leaves nothing and repeats cleanly |
| 9 | from a compile pod, install the fixture wheel from the mirror with `--no-index --find-links --require-hashes`, then with an altered hash | install succeeds, the altered hash fails |
| 10 | `nix develop -c just ci` | exit 0 |
| 11 | the `kind-smoke` job on `main` | one green run, the exit acceptance of this epic |
| 12 | S2 result file merged with pass or fail stated | host requirement table present, gate G1 decision recorded in `docs/spikes/S2-footprint.md` |

Rows 1 to 10 and 12 run on this host.
Row 11 runs on a GitHub runner, so it adds a second host size to the evidence, and it is a different run from the local one.
Not provable by this epic: the `dev` and `soak` presets starting on this host, real component footprints, Docker Desktop and non-WSL Linux.

## 7. Parallelism and cost

- Maximum parallel workers: 3 after the barrier (A.1, T.1 and H.3), then up to 4 on work that needs no cluster, with cluster work one at a time.
  The cluster is single-owner on this host, so every cluster-using issue takes `/tmp/wdf-cluster.lock` and runs one at a time, which caps real parallelism for lanes A, N, H, W, F and X.
- Host constraints: the `ci` cluster of six nodes on a 7.8 GiB host that already runs other work (4.9 GiB available when checked [ran]), so cluster runs wait for a quiet host, and a worker never starts `dev` or `soak`.
- Estimates, not measurements: lane 0 (1.5 d), lane A (3 d, the longest, A.3 canaries), lanes T, N, H and W (1 to 2 d each, mostly parallel), S2 (1 d timebox), exit lane X and the exit test (1 d).
  That is about 8 days elapsed and 14 worker-days of effort, with the cluster lock as the main source of slip.

## 8. Closure (filled at close)

Run on 2026-10-30 by a worker who did not write the lanes, on a clean `git archive` export of `main` at `0b8b522`, the merge of the sizing issue.
Host: WSL2 with 20,002 MiB `MemTotal`, 8 CPUs and `fs.inotify.max_user_instances` 512, shared with 13 containers and a plain kind cluster of the captain that stayed running and untouched.
Every cluster command ran under `flock /tmp/wdf-cluster.lock`, a watchdog sampled host `MemAvailable` every 5 s and would have removed only the cluster wdf below 1 GiB, and the lowest value sampled over the whole session was 13,769 MiB.
Real command output is in the pull request that records the run.

The row commands ran as written.
Row 4 first named `scripts/cluster/limits.sh`, which applies limits from a rendered file and exits 2 without one, so the row now names `allocatable.sh` and `canary.sh`, the scripts that check what the row expects.
Rows 1 to 10 were run on `ci` as the table says.
The `dev` and `soak` presets, which the spec could not start on the 7.8 GiB host, were additionally started one at a time on this host and torn down before the next.

| # | Result | Evidence |
| --- | --- | --- |
| 1 | pass | `just doctor` exit 0, `memory-vs-preset` reads `ok` (host 20,002 MiB, preset `ci` 8,832 MiB) |
| 2 | pass | `ci`, `dev` and `soak` render with nothing started, and two runs into one directory differ by nothing under `diff -r` |
| 3 | pass | from no wdf container, network or cluster, `just up` took 40 s and the second run 4 s with the same container ids and creation times |
| 4 | pass | `allocatable.sh` reports expected equal to actual on every node, and `canary.sh all` shows exit 137 with `oom_kill` raised, `Insufficient memory` for the over-request and `nr_throttled` raised |
| 5 | pass | pool labels and taints as the preset says, four namespaces each with a quota and a limit range, defaults admitted, over-quota rejected |
| 6 | pass | five profiles listed as the profile table says, the guard refuses the active context `kind-kind`, soak has no live-update step |
| 7 | pass | `ss -ltn` shows 18080 and 18081 on `127.0.0.1` only, the dummy endpoint answers 200 with Tilt stopped |
| 8 | pass | marker survived `just down` and `just up`, `just nuke` twice left nothing and the second run exited 0 |
| 9 | pass | the fixture wheel installs from the mirror with `--require-hashes`, an altered hash is refused |
| 10 | pass | `just ci` exit 0, also on `main` plus the three fix commits below |
| 11 | pass | `kind-smoke` run 41115019941 on `main` at `0b8b522`, green, 110 s on a runner with 16,367,524 kB `MemTotal` |
| 12 | pass | `docs/spikes/S2-footprint.md` states the result (fail on the 7.8 GiB host), holds the host requirement table and records the gate G1 decision |

Presets run for real on this host:

| Preset | Nodes | Node limits sum | `just up` | `just up` again | Canaries | Namespaces | Down and nuke |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ci` | 6 | 8,832 MiB | 40 s | 4 s, same ids | pass | pass | pass, nuke repeated cleanly |
| `dev` | 6 | 10,240 MiB | 39 s | 4 s, same ids | pass | pass | pass, nuke repeated cleanly |
| `soak` | 8 | 15,104 MiB | 43 s | 4 s, same ids | pass | pass | pass, nuke repeated cleanly |

An idle cluster costs about 1.0 GiB of host memory for `ci` and `dev` and about 1.25 GiB for `soak`, by `free` before and after `just up`.
A 300 s hold on `soak` with `tilt ci` deployed, and a `just up` under that state, changed no container.

Findings and what was done:

- `tilt ci` on `ci` timed out, because the dummy module pods had no node selector or toleration for the tainted pool nodes and stayed Pending.
  The four dummy modules now select and tolerate their pool, and `tilt ci` reports healthy workloads on `ci`, `dev` and `soak`.
- `canary.sh` exited on its first event poll when the scheduler had not yet written the event, because `kubectl` jsonpath fails on an empty list.
  It found the event in time on the first `ci` run and failed on the first `dev` run.
  The poll is retried now.
- The schedule canary requested a fixed 600 Mi, which fits the 1 GiB obs node of `dev`, so the pod ran instead of staying Pending.
  The request is now the live allocatable memory of the node plus 64 Mi.
- `just up` does not create the four namespaces, so row 5 needs `scripts/cluster/namespaces.sh apply <preset>` first.
- The `ci` profile with the real dummy modules enables no obs resource, because `dummy-obs` lists only `full` in `profiles`, and the fixture modules show the obs tier of `ci`.
- The recipes `verify`, `verify-clean` and `cluster-verify` still exit 3 with the stub text.

Not proven:

- Any component at its limit, because no preset was loaded with ballast on this host, so only the idle cluster, the canaries and the dummy workloads ran, and S2 was not repeated on this host.
- A soak of any length beyond the 300 s hold, because the soak criteria belong to characterization.
- CPU contention, because `dev` and `soak` ask for 13 and 20 CPUs on an 8 CPU host and only the canary throttle was measured.
- Egress of the compile pool, which reached `pypi.org` from the probe pod, because a network policy is a non-goal of this epic and the mirror README states intent only.
- Docker Desktop and non-WSL Linux, and the real Temporal, Postgres and object store footprints.

Closure addenda, one dated line each, are added after close when a `revisit` child issue is filed against this epic (see [docs/method.md](../method.md#revisiting-a-closed-epic)).

- 2026-10-31: the slots files of `ci`, `dev` and `soak` gained a `simulate` pool entry at 250m and 384Mi, still marked `estimate: true`, as a `revisit` child of this epic.
