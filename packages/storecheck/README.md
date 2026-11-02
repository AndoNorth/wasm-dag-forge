# storecheck

The loader of the store contract in `contracts/store/` and the S3 contract script that checks a store against it.

## Running the script

```sh
python -m wdf_storecheck run --endpoint URL --credentials FILE --mode base|access [--product seaweedfs|rustfs]
```

The exit code is 0 when every check passed or is a recorded known gap, 1 when a check failed or the endpoint is unreachable, and 2 on a usage error.
`--product` names the product whose known gaps `contracts/store/products.yaml` records, and without it every gap is a failure.

The credentials file is JSON with one entry per role of `contracts/store/roles.yaml`:

```json
{"admin": {"access_key": "...", "secret_key": "..."}, "submit": {"access_key": "...", "secret_key": "..."}}
```

`--mode base` needs only `admin`, which is the root identity of the store, and `--mode access` needs all nine roles.

| Mode | What it checks |
| --- | --- |
| `base` | 14 checks as `admin`: bucket creation and listing, a first write in every bucket, exact reads, ranges, overwrite, listing with a prefix and with pages, multipart upload and abort, delete, a key with spaces and non-ASCII characters, and a lifecycle round trip |
| `access` | every role other than `admin` against every bucket for put, get, list, multipart and delete, compared with the contract, then that a refused write leaves no object, a wrong secret and an anonymous client are refused, and that a writing role editing lifecycle rules is a `known-gap` or a failure |

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
