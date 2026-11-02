# Object store

The S3 compatible store of the platform, SeaweedFS pinned by digest in `deploy/versions.yaml`.
Modules: `objectstore` (the store) and `objectstore-bootstrap` (a host-side helper that creates the buckets and sets their lifecycle rules).
The contract it satisfies is [contracts/store/](../../contracts/store/README.md), and the script that checks it is `python -m wdf_storecheck`.

## The pod

One StatefulSet pod, `objectstore-0` in `wdf-infra` on the infra pool, holds three containers, each with its own readiness probe.
The pod is Ready only when all three are.

| Container | Runs | Readiness |
| --- | --- | --- |
| `store` | `weed server -s3` with `-volume.max=0` and the identities file | `GET /healthz` on the S3 port 8333 |
| `admin` | `weed admin` on loopback port 12535, with its state on the volume claim | `curl` of its own page, because it listens on loopback only |
| `worker` | `weed worker -jobType=s3_lifecycle`, which carries out the lifecycle rules | `GET /ready` on its metrics port 9327 |

The admin server and the worker start only once what they talk to answers, so no fixed sleep orders them.
Lifecycle rules are accepted by the store alone, but nothing expires without the admin server and the worker.

## Sizing

Requests and limits come from `docs/delivery/spikes/S8/state-footprint.md`, and the pod budget of `module.yaml` is split across the three containers in `Tiltfile.module`, which refuses a split that does not add up.
The `store` container has a soft Go heap limit below its container limit, so a growing heap is collected before the kernel kills the process.
The CPU limit of the whole pod is what is left of the `wdf-infra` quota of CPU limits, which equals the allocatable CPU of the infra node, after the other modules of the namespace.
A change to the CPU limit of any module in `wdf-infra` is checked against that quota by running `scripts/tilt/run.sh ci` and reading the events of the namespace.

The volume claim is 10,240 MiB.
`-master.volumeSizeLimitMB` is `floor(0.8 x claim / (7 x 4))`, which is 292 MB, because the first write to a bucket grows up to 7 volumes and there are four buckets.
Volumes are not preallocated, so the first write to each bucket is a check of the contract script.
The default storage class does not enforce the claim size: `df` inside the container reports the host file system, so the capacity share of a bucket store comes from the bytes in the buckets, as the capacity mode of the contract script reports.

## Data volume path

The data volume is mounted at `/data` in the `store` container.
The capacity input reads it with:

```sh
kubectl --context kind-wdf -n wdf-infra exec objectstore-0 -c store -- df /data
```

The claim also holds the state of the admin server and of the worker, in the `admin` and `worker` sub-directories, which the `store` container does not see.

## Identities

`scripts/storage/store/identities.sh` generates one credential for each role of `contracts/store/roles.yaml` on first use and keeps them in the state directory of the cluster scripts, in `objectstore.credentials.json`.
It renders the identities file of the store from the same roles into a generated Secret, so no credential is committed.
Each role other than `admin` carries a policy of exactly its grants, and the coarse `Write` action of SeaweedFS is never used, so a writer cannot edit lifecycle rules.
The contract script fails if that changes.
`admin` is the root identity, used by the bootstrap and by no pod.
`identities.sh path` prints the path of the credentials file that the contract script reads.

## Bootstrap

`scripts/storage/store/bootstrap.sh` waits for the pod, forwards `127.0.0.1` to it and, with the `admin` identity, creates the four buckets of `contracts/artifact/layout.yaml`, applies `contracts/store/lifecycle.yaml` to them and sets the detection interval of the lifecycle job to the 60 minutes of that file.
`bootstrap.sh check` exits 1 when the store differs from the contract.
Tilt runs `apply` as the resource `objectstore-bootstrap`, and both can be run by hand.

## Reaching the store from the host

Host access goes through a port-forward bound to loopback, and no host port is published:

```sh
kubectl --context kind-wdf -n wdf-infra port-forward --address 127.0.0.1 svc/objectstore 18333:8333
python -m wdf_storecheck run --endpoint http://127.0.0.1:18333 \
    --credentials "$(scripts/storage/store/identities.sh path)" --mode base
```

## Runbook

### Recovery from a full disk

A full store refuses puts with a 500, keeps every object it already holds and does not corrupt one.
Space freed by deleting objects returns to the store only after a vacuum.

1. Delete objects with the `reaper` identity, which lists and deletes in all four buckets, through the S3 batch delete.
2. Vacuum the volumes, which needs the cluster lock of the shell first:

   ```sh
   printf 'lock\nvolume.vacuum\nunlock\n' | \
       kubectl --context kind-wdf -n wdf-infra exec -i objectstore-0 -c store -- weed shell -master=127.0.0.1:9333
   ```

3. Put an object again to confirm.

The contract script's `recover` mode runs the same steps against a size-capped tmpfs.

### Rotating a credential

1. Delete `objectstore.credentials.json` from the state directory of the cluster scripts.
2. Render the new Secret and restart the pod, because the store reads its identities at start:

   ```sh
   scripts/storage/store/identities.sh render | kubectl --context kind-wdf apply -f -
   kubectl --context kind-wdf -n wdf-infra rollout restart statefulset/objectstore
   ```

3. Run `scripts/storage/store/bootstrap.sh check`, which needs the new `admin` credential and so proves the rotation.

Data and lifecycle rules live on the volume claim and survive the restart.

### A late or dead lifecycle worker

The lifecycle job looks for work every 60 minutes, so a rule runs up to an hour late.
A dead worker is a capacity alert and not a silent failure.
The pod then reads `2/3` ready, because the `worker` readiness probe fails, and expired objects stay in the buckets, so the bytes of `wdf-results` and `wdf-checkpoints` keep growing until the capacity alert at 70 percent fires.
Restart the worker by restarting the pod, and `bootstrap.sh check` confirms that the detection interval is still set.

### The lifecycle edit gap

SeaweedFS lets a credential with the coarse `Write` action replace the lifecycle rules of a bucket, which RustFS refuses.
The store avoids the gap by giving each role a policy of exactly its grants, so a refused edit is the expected result and `contracts/store/products.yaml` records no gap for SeaweedFS.
If an upstream change makes a policy stop refusing, the access mode of the contract script reports `FAIL` for the writing role.
