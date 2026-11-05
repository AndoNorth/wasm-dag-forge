# storecheck

The loader of the store contract in `contracts/store/` and the S3 contract script that checks a store against it.

## Running the script

```sh
python -m wdf_storecheck run --endpoint URL --credentials FILE --mode MODE [--state FILE] [--exec COMMAND] [--wait SECONDS] [--product seaweedfs|rustfs]
```

The exit code is 0 when every check passed or is a recorded known gap, 1 when a check failed or the endpoint is unreachable, and 2 on a usage error.
`--product` names the product whose known gaps `contracts/store/products.yaml` records, and without it every gap is a failure.

The credentials file is JSON with one entry per role of `contracts/store/roles.yaml`:

```json
{"admin": {"access_key": "...", "secret_key": "..."}, "submit": {"access_key": "...", "secret_key": "..."}}
```

`--mode access` needs all nine roles, and every other mode needs `admin`, which is the root identity of the store.
`capacity` also needs `reaper` and exits 1 without it.

| Mode | What it checks |
| --- | --- |
| `base` | 14 checks as `admin`: bucket creation and listing, a first write in every bucket, exact reads, ranges, overwrite, listing with a prefix and with pages, multipart upload and abort, delete, a key with spaces and non-ASCII characters, and a lifecycle round trip |
| `access` | every role other than `admin` against every bucket for put, get, list, multipart and delete, compared with the contract, then that a refused write leaves no object, a wrong secret and an anonymous client are refused, and that a writing role editing lifecycle rules is a `known-gap` or a failure |
| `lifecycle` | a past-dated `Date` rule on one prefix, one object under it and three controls outside it, polled until the object expires, which prints `expired: 1 of 1, controls kept: 3 of 3`, and the rule is taken out again |
| `days-arm` | a `Days` rule of one day on one prefix, two objects, and the time after which to check written to `--state` |
| `days-check` | refuses with `not yet due` and exit 1 before that time, then checks the armed object is gone and the control is kept, and removes the rule |
| `disk-full` | on a size-capped volume, 4 MiB objects until a put fails with `InternalError` and 500, a second refusal, every survivor byte exact, and a batch delete with `reaper` that works or is the recorded `delete-while-full` gap |
| `recover` | `disk-full`, then recovery through `--exec`, then a put that succeeds |
| `marker-put`, `marker-get` | one object of fixed bytes in `wdf-sources`, and a check after a restart that it is intact |
| `capacity` | one `bytes <bucket> <n>` line per bucket, summed from the listing of the `reaper` identity |

`--exec` is the command that runs inside the store, such as `docker exec -i ID` or `kubectl exec -i POD -c store --`.
`recover` feeds `lock`, `volume.vacuum` and `unlock` to `weed shell` through it on SeaweedFS and removes the first two fill objects from `/data` on RustFS.
`--wait` is how long `lifecycle` polls, 75 minutes by default.
The lifecycle job of the deployed store can be run at once by posting to `/api/plugin/job-types/s3_lifecycle/run` of its admin server, which ends the poll in about 30 seconds.

Each run writes under `storecheck/<run id>/` in the buckets and removes it afterwards.
The lifecycle probe puts the rules of the bucket back as it found them.

## Provisioning the roles

`wdf_storecheck.provision` creates the identities of the roles for the container tests.
Both products take the same IAM policy per role.
SeaweedFS reads them from an identities file mounted at start, and RustFS takes them through its signed admin API.
SeaweedFS is never given the coarse `Read`, `List` and `Write` actions, because `Write` also covers deleting and lifecycle edits.

## Tests

`pytest packages/storecheck -m "not integration"` needs no container.
`pytest packages/storecheck -m integration` starts one SeaweedFS or RustFS container at a time through `wdf_itest`.

## Fixtures

`wdf_storecheck.fixtures` is a pytest plugin registered through the `pytest11` entry point, so any package in the workspace gets the fixtures without a `conftest.py`.
`store` holds one container per product for a module and `store_session` holds one SeaweedFS for the whole session.
Both provision exactly the grants of `contracts/store/roles.yaml`, and SeaweedFS runs with `GOMEMLIMIT=512MiB` so 100,000 objects fit the 1 GiB container cap.
`running(t1, product, directory, tmpfs)` is the context manager under both, for a test that needs its own container.
A test that needs a store and a database together carries the markers `integration` and `pair`, which makes the `t1` session hold two containers.
