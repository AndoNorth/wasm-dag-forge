# Delivery method

How wasm-dag-forge epics are delivered one at a time.
Every epic goes through four steps: prepare, build, verify, close.
Prepare produces one file, the epic breakdown spec (`docs/epics/NN-<slug>.md`).
The captain reviews that file once, and that review is the alignment step for the domain.
Lane issues are filed on GitHub only after approval, and the other lanes start only after lane 0 is merged.

## Alignment rule

Before executing a domain, its breakdown spec is reviewed once by the captain.
Approval is the alignment, and no separate design meeting is held.
During execution, work relies on the architecture note, the pinned contracts and the epic.
When a domain closes, the note and the epic are refined with what was learned.

## Artifacts, one source of truth each

| Thing | Lives in | Form |
| --- | --- | --- |
| What a domain must deliver | the epic issue (label `epic`, GitHub Project 2) | GitHub issue |
| How to deliver it in parallel | `docs/epics/NN-<slug>.md` | repo file, reviewed as a PR |
| Why the system has this shape | `docs/architecture/` (draw.io files, one note per area as each domain executes) | repo files |
| Pinned contracts | `contracts/<name>/` | schema files, tables, test vectors, a loader test, `README.md` |
| Spike results | `docs/spikes/S<n>-<slug>.md`, throwaway code in `spikes/S<n>/` | repo files |
| Work units | GitHub issues labelled `lane:<x>` and `epic:<n>`, sub-issues of the epic | issue from the lane issue template |
| Verifiers (domains that need end to end driving) | skill in `.claude/skills/verify-<domain>/`, scripts in `scripts/verify/<domain>/` | agent skill plus scripts |

Filed as GitHub issues: epics, lane issues (after approval) and spikes (label `spike`, timeboxed).
Kept as files: breakdown specs, contracts, spike results, architecture notes and verifiers.
A breakdown spec is not a design spec, so it does not use the `spec-template` skill.
That skill stays for design decisions inside an architecture note.
Epic text and notes link to contracts and never copy them.

Templates:

- [Epic breakdown spec](templates/epic-breakdown.md)
- [Lane issue](../.github/ISSUE_TEMPLATE/lane-issue.md)
- [Pull request](../.github/pull_request_template.md)

The lane issue template is a markdown template, not an issue form, because workers file with `gh-axi issue create --body-file` and issue forms apply only in the web UI.

## One epic, step by step

### Prepare

1. The author reads the epic, its neighbours' Interfaces tables, `contracts/`, the architecture note and the verifier for the domain if one exists.
2. The author writes `docs/epics/NN-<slug>.md` from the template.
   The questions the captain must answer are the first section, at most five.
3. Before review, the read-only spec agents run and their findings are fixed: `spec-coherence`, `spec-citation-verifier`, `spec-ticket-coverage`, `spec-adversary`.
4. The captain reviews the spec PR once.
   Approve, comment, or answer the listed questions.
5. On merge, the lane issues are filed from the spec's issue blocks, the spec keeps only `#NNN` links, spikes are filed, and an epic-wide tracking comment is opened on the epic issue.

### Build

1. Lane 0 merges first, alone.
   It carries the pinned contracts, stubs for every cross-lane dependency, the justfile module for the domain and, where the domain has a verifier, the verifier skeleton.
   Lane 0 is the only barrier, and every other lane runs in parallel after it.
2. Each worker uses its own worktree and branch `epic-NN/<lane>/<issue>`, with one PR per issue, squash merged to `main`.
3. Spikes that gate lanes run before those lanes are filed.
4. A cross-lane change is a PR that touches `contracts/` only, labelled `contract-change`, with a comment listing the consumer issues.
   Consumers rebase.

### Verify

1. Per issue, the worker runs the issue's verifier (a named test, or `just verify <domain> <feature>` where the domain has a verifier), updates the feature file where one exists and pastes evidence in the PR.
2. Per epic, a different worker runs the exit test on a clean export of `main` and records the run in the spec's Closure section.
   Where the epic has no verifier, the exit test is the epic's stated checklist, run by a worker who did not write the lanes.

### Close

1. The definition of done below is fully ticked.
2. The architecture note is refined with what was learned.
   The epic issue gets a closing comment covering what was built, what moved and what was deferred to which epic, and is closed.
3. The next epic's breakdown inputs are updated: contract versions, verifier features now `scripted` where a verifier exists, changed estimates.

## Lane isolation and merging

1. One owner per path.
   The spec's lane table gives each lane exclusive globs.
   Stubs created in lane 0 belong to lane 0 until merged, then to the lane that replaces them.
2. Shared files are made disjoint by construction.
   - `justfile` only `import`s `just/<domain>.just`, and a domain edits its own module.
   - The `flake.nix` shell hook only sources `scripts/env.sh`, owned by the Python lane.
   - Each Python package is `packages/<name>/pyproject.toml`, owned by one lane.
   - `uv.lock` is the one unavoidable hotspot: a lock conflict is never resolved by hand, `uv lock` is rerun after rebase, and a dependency lands in the PR that needs it.
   - `deploy/versions.yaml` is the second hotspot: one key per line, and a PR touches only its own keys.
   - `Tiltfile` loads one `deploy/<component>/Tiltfile.module` per owner.
3. Every stub has a contract test that runs against the stub and against the real thing, so a stub cannot drift silently.
   Lane 0 writes the stub and the test, and the owning lane makes the real one pass it.
4. `main` stays green (`nix develop -c just ci`).
   There is no long-lived integration branch, and a lane that falls more than a day behind rebases.
   Within a lane, issues merge in order when the issue says `needs`, and across lanes any order works.
5. Containers use a worker slot: project name `wdf-<slot>`, host ports `127.0.0.1:<base+slot*10+k>`, label `wdf.dev/verify-run=<run id>`.
   The kind cluster is single-owner: every recipe that touches it takes `flock /tmp/wdf-cluster.lock`, and a verifier refuses to drive a cluster it did not start.
6. An out-of-lane edit is a review rejection, not a merge conflict to resolve.
   The worker comments on the issue and the spec author fixes the lane table.

## Contracts and spikes

Contracts are pinned in lane 0 of the epic that produces them, merged before any issue that consumes them is filed.
A pinned contract has a schema or table file, test vectors, a loader test that fails if a vector breaks, a `version` and an owner epic.
Changing it after the pin is a contract-change PR with a consumer list and a version bump.

Spikes are timeboxed, each one issue labelled `spike` with a binary pass line, throwaway code in `spikes/S<n>/`, and a result file `docs/spikes/S<n>-<slug>.md` stating pass or fail, the numbers and the decision it settles.
A spike belongs to the epic whose design its answer can change, and the lanes it gates are not filed until the result is merged.
Spikes that need a runtime but change no product file are the only work allowed alongside the one active epic.

## Definition of done per epic

1. Every lane issue is closed with acceptance criteria ticked and evidence linked, as pasted output per the PR evidence section below.
2. The exit test (spec section 7) passed from a clean export of `main`, run by a worker who did not write the lanes.
   Where the domain has a verifier, evidence is kept at `.verify-evidence/<domain>/<run id>/` and summarised in the spec's Closure.
   Where it has none, the command output is kept in the PR and summarised in Closure.
3. `just ci` is green on `main`, and when the epic added a CI job, one green run on `main` is linked in Closure.
4. Where the domain has a verifier, every feature the epic delivered is `scripted` and passed in step 2, and remaining features are `planned` with owner epic and reason.
5. Contracts produced are pinned at a version, and every consumer epic's spec links the version.
6. The architecture note is refined, and diagrams are re-exported if touched (`scripts/export-diagrams.sh --check` clean).
7. The epic issue carries a closing comment and is closed.
8. No `TODO` without an issue, and no stub left that a later epic does not own by name.

## PR evidence

A pull request body follows the [template](../.github/pull_request_template.md): Summary, Notable decisions, Test plan and Risks.
Every Test plan line is a checked box naming the exact command run, followed by a collapsed block holding the real output, response body or log lines, trimmed to the relevant part.
Evidence is output that was captured, never a description of it, and a UI or rendered output carries a screenshot or rendered text.
A criterion that cannot run yet says which later work it waits for.

## Comments

Code and config comments explain intent and never name an issue, epic or task, because those references rot.
Ownership and sequencing live in the issue, the epic spec and the PR.
A stub's runtime message and exit code are a recipe contract and may name an owner, but its comment does not.

## Revisiting a closed epic

A closed epic stays closed and its text is not rewritten.
When later work in another domain exposes a gap in an earlier, merged epic, the needed change becomes a child issue of that earlier epic (a GitHub sub-issue), labelled `revisit`.
The child issue:

- links the later epic or lane that exposed the gap
- lists the consumer epics affected
- names the verifier feature it touches
- blocks the later lane until it is merged

A change to a pinned contract is not a revisit issue by itself.
It is a contract-change PR with a consumer list and a version bump, as in the contracts section above.

The earlier epic's breakdown spec gets a dated Closure addendum line linking the child issue.
The later epic's breakdown spec lists the child issue under Consumes.

## The verifier convention

Domains that must be driven end to end have a project-local skill `verify-<domain>` modelled on the cursor [create-verification-skill](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md).
Skills are created with the epic that first needs them, as part of its lane 0.
None exist yet.
The dev environment (#1) is the deliberate exception.
Its surface is a shell, a command table and a host preflight, and `just doctor` with its fault-injection self-test, the lint fixtures, the recipe checks, the CI check that runs `just ci` and `docs/dev-setup.md` prove it without a driven journey.
Verifier scripts live under `scripts/verify/<domain>/` and the skill under `.claude/skills/verify-<domain>/`.

### Shape

- Path: `.claude/skills/verify-<domain>/` with `SKILL.md`, `features/README.md` and `features/<feature>.md`, and the scripts in `scripts/verify/<domain>/`.
- `<domain>` names a system area a user or an agent touches, not an epic number.
- One executable entry: `just verify <domain> [feature]` runs `scripts/verify/<domain>/run.sh`.
  The skill text and the script say the same thing: the script is what a worker runs, the skill is what a cold agent reads.
- Evidence goes to `.verify-evidence/<domain>/<run id>/` (gitignored), with `<run id>` = `<UTC timestamp>-<slot>`.
  Cleanup never removes evidence.
  A `summary.json` (run id, commit sha, tier, feature results, skip reasons) is pasted into the PR or the spec's Closure.

### Required content of `SKILL.md`, in order

1. Frontmatter: `name`, `description`.
2. Surface and tiers.
   - T0 fixtures: no runtime, covering schema validation, golden vectors, CLI exit codes and `nix flake check`.
   - T1 containers: real Postgres, RabbitMQ, Temporal dev server and S3 endpoint by `docker run` at the digests in `deploy/versions.yaml`, slot-isolated, plus real `wasmtime` and `componentize-py`.
   - T2 cluster: `just up` on preset `ci`.
   - Each feature declares its lowest tier, and proof means exercising the real deliverable at that tier, never a mock of it.
3. Isolation: slot, ports bound to 127.0.0.1, lock file, whether two runs can coexist, and the refusal rule that a run never drives an instance it did not start.
4. Launch, 5. Doctor, 6. Drive, 7. Evidence, 8. Cleanup, 9. Helpers, as in the cursor skill.
   Cleanup removes only resources carrying this run's label or recorded PID, and never uses `docker rm` or `kill` by name, `nix-collect-garbage`, or removes evidence.
10. Feature status table: every row has `Tier`, `Epic` and `Status` in `{scripted, manual, planned}`, and `planned` rows state the unmet precondition.

Each feature file has exactly four H2 sections as in the cursor feature map (`Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, `Gotchas`), with the metadata as a line under the H1.

### Creating one when little exists

The verifier skeleton is part of lane 0, so it exists before the other lanes start.

1. Write the skill text for what exists today.
   If nothing runs yet, Launch says nothing starts and a clean tree is exported, which is a valid complete section.
2. Doctor checks only what is true on day one: tool present, lock free, scratch directory writable, host prerequisites.
3. Seed the feature map from the epic's feature list (3 to 8 features), every row `planned` except one.
4. Make that one feature `scripted` and prove the whole loop on it: launch, doctor, drive, evidence, cleanup, evidence still present.
5. Prove features in this order: fixtures with known answers, real tools in a scratch export, real dependencies in containers, then the cluster.
6. A lane that needs a counterpart that does not exist yet uses the lane-0 stub and marks the feature `planned: needs <epic>`.
   A journey is never weakened to pass.

### Keeping it honest

- Every issue carries a verifier delta where the domain has a verifier: a PR that changes behaviour without touching a feature file, or saying why not, is sent back.
- Map lint (`just verify-lint`, part of `just ci`) checks that every feature file has the four H2 sections, every `scripted` feature points to an executable script, and every recipe and every contract file is referenced by a feature or listed in `verify-allowlist.txt` with a reason.
- Where the domain has a verifier, epic close runs the whole verifier from a clean export, by a worker who did not write the lanes.
  The closing worker also reads the feature map against merged code and fixes drift.
- A failing journey is fixed, or recorded in Gotchas and set `planned` with a reason, in the same PR that notices it.

### What a worker runs before reporting done

1. `nix develop -c just ci`.
2. `just doctor`, and the domain verifier's doctor where the domain has a verifier, both ok.
3. `just verify <domain> <feature>` for every feature named in the issue's Verifier field, or the named test where the issue names one.
4. Where the domain has a verifier, `just verify-clean <domain>`, which lists any resource still carrying the run label (must be empty) and confirms the evidence directory exists.
5. Report the commit sha, evidence path where there is one, per-feature result, and explicitly what was not run and why.
   A report that claims done without step 3 is not done.

### The 10 verifiers

| Verifier | Epics | Created at | Tiers when created | Can verify now | Cannot yet |
| --- | --- | --- | --- | --- | --- |
| `verify-cluster` | #2 | E2 | T2 | Yes, on host capacity: one cluster, S2 decides which preset fits | `dev` and `soak` presets on a 7.8 GiB host |
| `verify-job-model` | #4, #5 | E3 | T0 | Yes (pure libraries, fixtures, CLI) | Nothing blocking |
| `verify-storage` | #3, #12 | E5 | T1 | Yes (TimescaleDB and S3 containers) | Compile cache and reaper timing until #11 and #10 exist |
| `verify-workloads` | #11 | E7 | T0, T1 | Yes (real `componentize-py`, `wasmtime`, kernel golden vectors) | Pause and resume workloads until #18 |
| `verify-dispatch` | #24, #7, #8 | E10 (broker first) | T1, then T2 | Broker and API at T1, admission with a Temporal dev server | Full submit to workflow start through the cluster until the gate |
| `verify-execution` | #6, #9, #10 | E8 | T1, then T2 | Workflow, placement, runner at T1 | Pool behaviour, OOM 137, node placement, rollout (T2) |
| `verify-observability` | #13 | E15 | T2 | No | All of it until the gate |
| `verify-load` | #14, #16 | E18 | T0 (stub API), then T2 | #14 scenario validation and ledger at T0 | #16 baselines and soak |
| `verify-resilience` | #15, #18 | E16 | T2 | No | All of it until #6, #8, #10, #12 exist, injector library alone at T1 |
| `verify-system` | #17 | gate | T2 | No: it is the smoke and e2e suite, assembled from the others | Until the first smoke at the gate |

One verifier covers each surface a user or agent drives, except the dev environment, which has none for the reason given in the convention above.
The broker and admission sit behind the dispatch API, so they merge with it.
`verify-system` is not a separate implementation: its Drive section calls the other verifiers' features in the order the acceptance flow in #17 gives.

## Epic order

Order follows the needs-first arrows between epics, with one risk-first adjustment: the three runtime spikes (S3, S4, S5) run right after #2, because a failed spike changes the design of epics that would otherwise already be built.
An epic is opened only when everything it consumes is closed or stubbed in lane 0.
Where owners and consumers form a cycle (#10 with #11, #3 with #12), a lane-0 stub breaks it.

| Pos | Epic | Verifier | Contracts pinned in its lane 0 | Spikes |
| --- | --- | --- | --- | --- |
| E1 | #1 Dev environment | none, covered by `just doctor`, CI and `docs/dev-setup.md` | `versions.yaml` schema, `just` recipe table | none |
| E2 | #2 Cluster and Tilt | `verify-cluster` | preset schema, module contract, pool label and taint, namespaces | S2 footprint (#2-13), gate G1 |
| E2.5 | spike block | none | none | S3 Temporal with Python (owner #6), S4 AMQP 1.0 client (#7), S5 object store (#3-8) |
| E3 | #4 Envelope | `verify-job-model` | tranche A: events and keys, status, artifact reference and bucket layout, instrumentation contract (#13-1) | none |
| E4 | #5 Validator | `verify-job-model` | job document and plan JSON Schema, limits profile | none |
| E5 | #3 State and storage | `verify-storage` | accepted-jobs row DDL, event hypertable, store contract | S5 result applied |
| E6 | #12 Artifacts | `verify-storage` | aggregate index format, failure taxonomy | none |
| E7 | #11 Toolchain, guest contract, workloads, aggregate | `verify-workloads` | WIT and guest contract, `build-info.json`, cache entry | S1b heavy compile (#11-15), S6 cold-start storm (#11-9) |
| E8 | #6 Workflow engine | `verify-execution` | activity contract, failure class table, Temporal naming, static pool table v0 | S3 result applied |
| E9 | #9 Placement | `verify-execution` | pool table (real values), timeout formulas | none |
| E10 | #7 Broker | `verify-dispatch` | tenant, class and cap config format, DLQ reason taxonomy | S4 result applied |
| E11 | #10 Workers | `verify-execution` | none new | none |
| E12 | #8 Admission | `verify-dispatch` | none new | none |
| E13 | #24 Dispatch and control API | `verify-dispatch` | API routes, error body, reason codes, status response | S7 100k submit (optional lane) |
| Gate | #17 pass 1: smoke | `verify-system` | smoke recipe | none |
| E15 | #13 Observability | `verify-observability` | catalogue (already pinned in E3) | none |
| E16 | #17 pass 2: e2e | `verify-system` | acceptance fixtures | none |
| E17 | #18 Checkpoint and resume | `verify-resilience` | checkpoint contract v1 | none |
| E18 | #15 Failure scenarios | `verify-resilience` | none | none |
| E19 | #14 Load CLI | `verify-load` | scenario schema | none |
| E20 | #16 Characterization and soak | `verify-load` | baseline report format | none |

The E numbers are positions, not epic numbers: E14 is the smoke gate and E2.5 is a block.
The gate is `just smoke`, one level 0 job from #24 through #8, #6, #9, #10 and #12 to a stored result, with ten consecutive passes before the next epic opens.

Where each epic sits, in one line each:

- #2 settles host fit, the one finding that can stop the PoC on this machine, and ships the module contract and a dummy module, while each real infra module is delivered by its owner epic.
- The spike block is three independent spikes, one worker each, writing only under `spikes/` and `docs/spikes/`.
- #4 is a pure library needing only #1, and its contracts are consumed by almost everything after it.
- #7 can sit anywhere before #8, and sits at E10 because the S4 result is known by then and the host is free.
- #11 defers `paced_sim` and kill-and-resume (issues 5 and 12) to #18's pass, and S6 feeds #10 budgets.
- #18 is not ready until the base is proven, so it follows the smoke gate and e2e.
- #16 is last by design: it needs the #13 series, #14, #15, a green smoke and the host decision.

### Where spikes and contract items go

| Item | Goes to | When |
| --- | --- | --- |
| S1b heavy compile | #11 issue 15, in `verify-workloads` | E7, first lane |
| S2 footprint | #2 issue 13 | E2, exit |
| S3 Temporal with Python | #6 issue 1 plus a new spike issue | spike block, result applied in E8 |
| S4 AMQP 1.0 client | #7 issue 3 | spike block, result applied in E10 |
| S5 object store (settles D10) | #3 issue 8 | spike block, result applied in E5 |
| S6 cold-start storm | #11 issue 9 | E7, first lane, feeds #10 and a possible #2 preset edit |
| S7 100,000-row submit | #24 optional lane | E13 |
| Contract 1 Events and keys | #4 lane 0 | E3 |
| Contract 2 Status (D12 decided in the E3 review) | #4 lane 0 | E3 |
| Contract 3 Artifacts (reference, buckets, `build-info.json`; the aggregate index format is written by #12) | #4 lane 0, index in #12 lane 0 | E3, E6 |
| Contract 4 Activity (payloads, failure class table, heartbeat details) | #6 lane 0 | E8 |
| Contract 5 Pools and Temporal naming | #6 lane 0 (static v0), #9 lane 0 (real) | E8, E9 |
| Contract 6 API and config (route table, error body; tenant and class config) | config in #7 lane 0, API in #24 lane 0 | E10, E13 |
| Contract 7 WIT | #11 lane 0 | E7 |
| Instrumentation contract (#13 issues 1, 2) | #4 lane 0, pulled forward | E3 |

Contract 3's artifact reference is needed by #4 (result ready event) before #12 runs, so it is pinned in E3.
That is the only place a contract is pinned before its owner epic.

### Allowed overlaps

Exactly three, each with a rule that keeps it small:

1. #1 and #2 share one issue: the `kind smoke` CI job moves to #2's exit and is that epic's acceptance, and #1 closes with the lint, test and flake-check jobs.
2. #17 is delivered in three passes.
   Pass 1 (smoke) is the gate and pass 2 (e2e) follows #13.
   The ladder document and pytest harness skeleton exist as lane 0 of #1, since the `test` and `itest` recipes are #1's.
3. The spike block and pulled-forward contracts are allowed because they touch only `spikes/`, `docs/spikes/` and `contracts/`, never product code.

An epic may not overlap another on product files.
If a fourth overlap appears, the later epic's spec states why in its section 5, and the captain sees it in the one review.

### Critical path

`#1, #2, #4, #3, #12, #11, #6, #10, #8, #24, smoke, #18, #16`.
The two largest risks are decided earliest: host fit (S2, E2) and the Temporal pattern (S3, spike block).
If S2 fails, the next epic is not opened, and the epics that need no cluster (E3, E4) can continue while the host decision is made.
