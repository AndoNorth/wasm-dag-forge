# Epic #N: <name> - breakdown

Status: draft | approved <date> | closed <date>
Epic: <issue url>
Architecture note: docs/architecture/<file>
Verifier: .claude/skills/verify-<domain>/
Depends on (closed epics): #a, #b

## 0. Decisions for the captain (max 5)

<question, options, recommendation, default if no answer>

## 1. Goal and non-goals

<2 to 4 lines, copied from the epic's Goal, plus anything this pass defers and to which epic>

## 2. Interfaces

### Consumes (each pinned to a version)

Open `revisit` child issues of earlier epics that block this epic's lanes are listed here too.

| From | What | Pinned at | Stub used until it exists |
| --- | --- | --- | --- |

### Produces (each pinned precisely)

| To | What | Where it lives (path) | Shape (schema file, signature, exit codes, key names) | Version |
| --- | --- | --- | --- | --- |

## 3. Lanes

| Lane | Worker count | Owns (path globs, exclusive) | Issues | Needs |
| --- | --- | --- | --- | --- |

Shared-file rules: <only files two lanes may touch, and the merge rule for each>

## 4. Issues

One block per issue, in the [lane issue template](../../.github/ISSUE_TEMPLATE/lane-issue.md).
Replaced by `#NNN` links after filing.

## 5. Order, stubs and spikes

<barrier lane, DAG of lanes, each stub: what it fakes, how its conformance test runs against the real thing>
<spikes: id, question, pass line, what it blocks>

## 6. Verifier delta

<features added or flipped planned -> scripted by this epic, tier for each, new helper scripts, what stays planned and why>

## 7. Exit test

<numbered, each step has a command, an expected result and the evidence file>

## 8. Parallelism and cost

<max workers per phase, host constraints (cluster is single-owner), estimates marked as estimates>

## 9. Closure (filled at close)

<commit sha, verify run id, evidence path or CI run url, what was learned, what moved>

Closure addenda, one dated line each, added after close when a `revisit` child issue is filed against this epic:

- <date>: <link to the revisit issue>, <one-phrase reason>
