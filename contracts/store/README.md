# Store contract

Owner epic: #3 (durable state and artifact storage).
Version: 1
Consumers: artifacts (#12), the contract script of this epic, the object store module, characterization (#16, #17) and the observability epic (#13).

This directory pins who may do what in the object store, which lifecycle rules each bucket carries and what each tested product is known not to do.
The four bucket names are read from [../artifact/layout.yaml](../artifact/layout.yaml) and never retyped, and the loader fails when a bucket is missing or extra.
The models live in `packages/storecheck/` (import `wdf_storecheck`), and [store.schema.json](store.schema.json) is generated from them by `python -m wdf_storecheck.gen`, whose `--check` fails on drift.
The product is SeaweedFS 4.51 pinned by digest in `deploy/versions.yaml`, with RustFS 1.0.4 as the tested second target of the same contract script.

## Roles

[roles.yaml](roles.yaml) names nine identities and, for each, the actions it may take on each bucket it names.
The actions are `get`, `put`, `list`, `multipart`, `delete`, `bucket` and `lifecycle`.

| Role | Used by | Grants |
| --- | --- | --- |
| `admin` | the bootstrap, and no pod | `bucket` and `lifecycle` on every bucket |
| `submit` | the submit path | writes `wdf-sources` |
| `indexer` | the aggregate index builder | writes `wdf-sources` and nothing else |
| `compile` | the compile stage | reads `wdf-sources`, writes `wdf-components` |
| `simulate` | the simulate stage | reads `wdf-components`, writes `wdf-results`, reads and writes `wdf-checkpoints` |
| `aggregate` | the aggregate stage | reads and writes `wdf-results` |
| `workflow` | the workflow | reads `wdf-sources`, `wdf-components` and `wdf-results` |
| `export` | the evidence export | reads `wdf-results` |
| `reaper` | the artifact reaper | lists and deletes in all four buckets |

The split stays at bucket level, and prefix-level restriction is not tested.
Only `reaper` deletes and only `admin` provisions, and a test holds the file to both.

## Lifecycle

[lifecycle.yaml](lifecycle.yaml) lists the rules of each bucket.
Each rule names a window by its key in [../state/retention.yaml](../state/retention.yaml), so a number lives in one place.
Every bucket has at least one rule, and every bucket aborts incomplete multipart uploads.
Results and checkpoints also expire by age, and sources and components have no age rule because their keys are content-addressed and shared across jobs.
The detection interval of the lifecycle job is 60 minutes, so a rule runs up to an hour late, and a dead worker is a capacity alert and not a silent failure.

## Products

[products.yaml](products.yaml) names the tested products, each by a key of the images map of `deploy/versions.yaml`, and the known gaps of each.

- `lifecycle-edit-by-writer` on SeaweedFS: a credential with `Write` on a bucket can also replace that bucket's lifecycle rules, which RustFS refuses.
  Bucket credentials belong to platform pods only, and the contract script records the gap per writing role and fails the day the behaviour changes either way.
- `delete-while-full` on RustFS: a full volume cannot be emptied through S3 alone, which SeaweedFS can do with a batch delete.

## Loader

[check.sh](check.sh) validates one of the three files and exits 0 when valid, 1 with `reason: <code>: <detail>` on stderr and 2 on a usage error.
The reasons are `schema-violation`, `unknown-bucket`, `role-outside-matrix`, `secret-in-contract` and `bucket-without-rules`.
`role-outside-matrix` covers a role that is not one of the nine, a role with no grants and a known gap that names an unknown role.
`secret-in-contract` covers any key at any depth whose name reads as a secret, so a credential is never committed here.
Vectors are under [vectors/](vectors/), and the file of each invalid vector sits in the directory named by its reason.

## Changing the contract

A change to a model, a role, a rule or a gap after the first merge is a contract-change PR with a consumer list and a bump of `VERSION`.
That PR carries the contract file, the model under `packages/storecheck/` and the regenerated schema together, because the drift check fails otherwise.
