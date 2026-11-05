# wdf-artifactreaper

The reaper of the artifact passing epic: retention of artifact objects and their index rows.

## State

`reap_once` runs the passes that act on results, checkpoints, running jobs and the compile cache.
The collection of content-addressed blobs and the Temporal Schedule are stubs.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## One run

`reap_once(conn, client, now, windows, heartbeat=None, dry_run=False)` takes an autocommit connection of the `wdf_reaper` role and a boto3 client of the `reaper` store role.
Every age is judged against `now`, so repeating a run with the same `now` finds nothing more to do.
A session advisory lock keeps two runs from overlapping, and the later one returns a report with `ran` false.

| Pass | Selects | Does |
| --- | --- | --- |
| Results | terminal jobs past their results window, within the hard expiry of the bucket, with no `results_done_at` | marks the rows deleted, removes every object under the job prefix and the aggregate prefix, then sets the marker |
| Checkpoints | the same with the checkpoint window | the same in the checkpoint bucket, with `checkpoints_done_at` |
| Prune | running jobs with more live checkpoints of a task than the keep count | removes the objects under the segment of each surplus checkpoint, then marks its row |
| Cache | live entries last used before the cache window | retires the entries, which frees their keys |

A paused, admitted or resuming job is never selected by any pass.
A heartbeat follows each batch of 500 objects.

## Contract files

The package carries the layout and the failure taxonomy of `contracts/artifact/` as package data.
The retention windows are read from the packaged files of `wdf_artifactclient`, and `load_windows()` checks them against the safety net of the store.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/artifactreaper -q -rs
nix develop -c uv run --frozen python -m wdf_artifactreaper run-once --dry-run --dsn DSN --endpoint URL --credentials FILE
```

`run-once` prints one line per counter and exits 0, 1 for a failed pass, 2 for usage and 3 when the database is unreachable.
