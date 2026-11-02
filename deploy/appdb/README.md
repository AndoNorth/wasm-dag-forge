# App database

The app Postgres with TimescaleDB, which holds the accepted-jobs table and the event history.
Modules: `appdb` (the database) and `appdb-migrate` (a host-side helper that applies the schema migrations).

## Image and license

The image is the official `timescale/timescaledb` image, pinned by digest in `deploy/versions.yaml`.
Its compression and policy features are under the Timescale License, which is source-available and not open source.
The proof of concept accepts that license, and telemetry is off by setting (`timescaledb.telemetry_level=off`).

## Sizing

Requests and limits and the 4,096 MiB volume claim come from `docs/delivery/spikes/S8/state-footprint.md`.
`shared_buffers` and `max_connections` are set explicitly, because the image tunes itself to the container limit only on the first start of an empty volume.
The volume lives on the host data directory of the preset, so it survives `just down` and is removed by `just nuke`.

## Credential

`scripts/storage/db/secrets.sh` generates the superuser credential on first use into the state directory of the cluster scripts and prints it as a Secret for Tilt.
It is never committed.
A credential file deleted while the volume survives no longer matches the initialized database, and `just nuke` is the way out.

## Migrations

`scripts/storage/db/migrate.sh` waits for the pod, forwards `127.0.0.1` to it and runs `wdf_migrate apply`.
Tilt runs it as the resource `appdb-migrate`, and it can be run by hand.

## Disk probe

`scripts/storage/db/fsync.sh` runs `pg_test_fsync` in the pod on its volume claim and prints `df` of the data volume.
