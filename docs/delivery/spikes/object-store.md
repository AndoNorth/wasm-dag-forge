# Spike: object store for the durable state domain (S5)

Run on 2026-10-27.
Owner: the [durable state and artifact storage epic](https://github.com/AndoNorth/wasm-dag-forge/issues/3).
The characterization epic ([system characterization and soak tests](https://github.com/AndoNorth/wasm-dag-forge/issues/16)) owns the retention check named below.

## Question

Which S3-compatible store replaces MinIO?
The gate is 14 of 14 base contract checks with real admin credentials and a refused write with the wrong role.
The rule is the first product that passes, in the order SeaweedFS then RustFS.

## Environment and versions

| Item | SeaweedFS | RustFS |
| --- | --- | --- |
| Image | `chrislusf/seaweedfs:4.51` | `rustfs/rustfs:1.0.4` |
| Digest | `sha256:f7a49e3f3972ebed5be2ed4befa4a0c68eb7bdddba6e692d42729ceb545134b8` | `sha256:ed9210d6411d77ffceb70d94b964a3cb7d225983368bc25bebe01e995293dcc2` |
| Licence | Apache-2.0 | Apache-2.0 |
| Release used | 4.51 (2026-10-25) | 1.0.4 (2026-10-25) |
| Repo created | 2014-07-14 | 2023-11-23 |

Host: WSL2, 7.8 GiB RAM, Docker 29.1.3.
Client: boto3 1.43.119, path-style addressing, Python 3.14.
Each container had `--memory 1g --cpus 2` with ports on 127.0.0.1 only, and the full-disk runs used a 128 MiB tmpfs.

## Method

- One contract script runs five modes: base, access, lifecycle, disk full and recover.
- The access mode covers 4 roles x 4 buckets x put, get, list, multipart, delete, plus a bad secret and an anonymous client.
  The roles are submit, compile, simulate and aggregate, with bucket-level read and write sets.
- Lifecycle expiry used a past-dated `Date` rule on one prefix, with three controls in other prefixes and buckets, polled by `HEAD` every 5 s.
- Disk full wrote random 4 MiB objects until a put failed, then checked refusals, delete and recovery.

## Results

| Gate | SeaweedFS 4.51 | RustFS 1.0.4 |
| --- | --- | --- |
| Base checks | 14 of 14 | 14 of 14 |
| Wrong-role write refused | yes, `AccessDenied/403`, nothing left behind | yes, same |
| Access matrix | 76 pass, 4 fail | 80 pass, 0 fail |
| Lifecycle expiry observed | yes, 30 s after the worker ran | yes, 5 s |
| Idle memory | 69 MiB (131 MiB with admin and worker) | 64 MiB |
| Memory after the tests | 266 MiB | 119 MiB |
| Disk full, first refusal | after 29 x 4 MiB, about 12 MiB held back, `InternalError/500` | after 31 x 4 MiB, 0 free bytes, `InternalError/500` |
| Delete while full | batch delete works, 14 of 14 gone | fails, 15 errors, single delete returns 500 |
| Put again within 120 s | fail (123 s) until `volume.vacuum` | fail (125 s) until 8 MiB is freed outside S3 |
| Surviving objects byte exact | 29 of 29 | 31 of 31 |
| S5 gate | pass | pass |

The 4 SeaweedFS access failures are one per role: a credential with `Write` on a bucket can replace that bucket's lifecycle rules, and RustFS refuses this.
Neither product restarted, corrupted an object or accepted a write silently when full.

## Findings that change the design

1. SeaweedFS is chosen by the rule, and RustFS 1.0.4 is a valid drop-in alternative with the same contract script.
2. Lifecycle expiry in SeaweedFS needs `weed admin` and a `weed worker` (job `s3_lifecycle`) beside the store.
   The default cadence is once a day (`detection_interval_minutes: 1440`), and without them rules are accepted and read back but nothing expires.
3. SeaweedFS grows up to 7 volumes per bucket on first write.
   With a volume limit of 20 the fourth bucket's first write returned `InternalError/500`, so the store needs `-volume.max=0` or a deliberate volume size limit sized against the volume claim.
4. Space freed by deleting objects returns to SeaweedFS only after a vacuum, so recovery from a full disk needs `volume.vacuum`.
5. RustFS needs no extra process for expiry, no per-bucket volumes and no vacuum, but a full volume cannot be emptied through S3 alone.
6. Capacity alerts belong well below 90 percent, since SeaweedFS holds back about 12 MiB of 128 MiB and RustFS runs to 100 percent.
7. The bucket-level role split is enforced by both products.
   Prefix-level restriction was not tested.
8. RustFS maturity is the larger licence concern: a young codebase operated by a company under a contributor licence agreement.
   SeaweedFS has a commercial Enterprise Edition beside the Apache-2.0 core, which is not a blocker for a proof of concept.
9. The `minio/mc` client is no longer pullable, and RustFS administration uses its signed admin API.

## Caveats and what was not measured

- A `Days` rule on real aged objects was not observed, only a past-dated `Date` rule.
  Retention needs `Days`, so a 25 hour check belongs in the characterization epic.
- SeaweedFS expiry can be up to a day late at the default cadence unless the interval is lowered, which also costs scans.
- Abort of incomplete multipart uploads by lifecycle was accepted but not observed on either product.
- One node, no replication, one disk, and tmpfs instead of a Kubernetes volume claim.
- Not tested: restart while full, throughput, objects beyond 12 MiB, versioning, and a fix for the role-can-edit-lifecycle gap in SeaweedFS.
- `s3_contract.py` swapped to a volume claim in kind is the starting point for the storage regression suite.

## Decision

SeaweedFS 4.51 pinned by digest, with RustFS 1.0.4 as the documented alternative.
