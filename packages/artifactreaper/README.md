# wdf-artifactreaper

The reaper of the artifact passing epic: retention of artifact objects and their index rows.

## State

`reap_once` runs the passes that act on results, checkpoints, running jobs, the compile cache and content-addressed blobs.
The Temporal Schedule is a stub.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## One run

`reap_once(conn, client, now, windows, heartbeat=None, dry_run=False, hooks=None)` takes an autocommit connection of the `wdf_reaper` role and a boto3 client of the `reaper` store role.
Every age is judged against `now`, so repeating a run with the same `now` finds nothing more to do.
A session advisory lock keeps two runs from overlapping, and the later one returns a report with `ran` false.

| Pass | Selects | Does |
| --- | --- | --- |
| Results | terminal jobs past their results window, within the hard expiry of the bucket, with no `results_done_at` | marks the rows deleted, removes every object under the job prefix and the aggregate prefix, then sets the marker |
| Checkpoints | the same with the checkpoint window | the same in the checkpoint bucket, with `checkpoints_done_at` |
| Prune | running jobs with more live checkpoints of a task than the keep count | removes the objects under the segment of each surplus checkpoint, then marks its row |
| Cache | live entries last used before the cache window | retires the entries, which frees their keys |
| Collect | live content-addressed rows that no live cache entry, no use row and no live job holds, older than the unreferenced grace | tombstones the row, and in a later run, once it has stayed tombstoned for the interval and is still unreferenced, records `purged_at` and removes the object |
| Orphan sweep | objects of `wdf-sources` and `wdf-components` older than the orphan grace that have no live row | removes the object, from a cursor that bounds a run to a number of listing pages |

A paused, admitted or resuming job is never selected by any pass.
A heartbeat follows each batch of 500 objects.

## Content-addressed blobs

The collector never decides outside the row lock.
The tombstone, the purge and the sweep of a row that exists take the lock of the row through `wdf_artifactindex` and evaluate `unreferenced_content` again under it, and the object is removed before that transaction ends.
A link, a commit or an index of the same bytes that waits for the lock then sees the purge and puts the bytes again.
An object with no row at all is removed without a lock, and the window that leaves ends in the typed `ARTIFACT_LOST` that the cache rule handles.
The sweep treats a tombstoned row as an orphan only once the tombstone interval has passed, so the interval of the collector is not bypassed in the same run.
The cursor of the sweep is one row of `reaper_cursor` per bucket, and an empty position means the next run starts from the beginning.

`hooks` maps a name of `content.HOOK_POINTS` to a callable that receives the uri at that point, so a test can run another caller exactly there.
An unknown name raises.

## Contract files

The package carries the layout and the failure taxonomy of `contracts/artifact/` as package data.
The retention windows are read from the packaged files of `wdf_artifactclient`, and `load_windows()` checks them against the safety net of the store.

## Commands

```sh
nix develop -c uv run --frozen pytest packages/artifactreaper -q -rs
nix develop -c uv run --frozen pytest packages/artifactreaper -q -rs -m isolated -k orphan
nix develop -c uv run --frozen python -m wdf_artifactreaper run-once --dry-run --dsn DSN --endpoint URL --credentials FILE
```

`run-once` prints one line per counter and exits 0, 1 for a failed pass, 2 for usage and 3 when the database is unreachable.
