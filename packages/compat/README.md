# wdf-compat

The compatibility rules of the event contract, imported as `wdf_compat`.
The contract itself is [contracts/events/README.md](../../contracts/events/README.md), and the event models are in `packages/events/`.

## What a minor may change

A minor of a data schema may add an optional field, and may loosen a constraint.
Readers of the earlier minor ignore the added field, because an unknown minor of a known major resolves to the highest minor the reader knows.

## What needs a new major

A new major is a new `type`, and each of these changes needs one:

- removing a field
- renaming a field, which is a removal plus an added field
- adding a required field, or making an optional field required
- narrowing a type or an enum, so that a value the old schema accepted is rejected
- tightening a constraint (a smaller maximum, a larger minimum, a different pattern)

`breaking_changes(old, new)` finds these in two schemas and returns one message per change.
`tests/test_schema_evolution.py` applies it to the model of every type against its committed schema, and to each consecutive pair of minors of a major, so a change that wedges a replayed event fails the test and names the field.

## Reading

`read_event(data) -> Event` returns an event at the current major of its type, where the current major is the highest one with a schema file.

| Event | Result |
| --- | --- |
| current major | read as it is |
| previous major, window open | upgraded by the function registered for that step, then validated against the current major |
| previous major, window over | `UnsupportedMajor`, reason `retired-major` |
| previous major, no upgrade registered | `UnsupportedMajor`, reason `no-upgrade` |
| any other major | `UnsupportedMajor`, reason `unsupported-major` |
| invalid at its own major | `SchemaInvalid` |

An upgrade function takes the decoded event of major N and returns the decoded event of major N + 1.
It never changes `id`, `source` or `time`, so the event store still dedups, and `read_event` rejects an upgrade that does with `SchemaInvalid`, reason `upgrade-changed-identity`.
Upgrade functions are registered in `UPGRADES` of `reader.py`, one per major step and never chained across a gap.
Each upgrade increments `wdf_event_upgraded_total`.

## Replay

`upgrade_for_replay(data) -> bytes` is the pure function for a replayed dead letter that meets a newer reader.
An event of the current major returns byte-identical, so the original bytes are republished.
An event of the previous major returns as canonical bytes of the current major, with `id`, `source` and `time` unchanged, so the history row still dedups.
It follows the table above, and it returns the whole upgraded event or raises, never bytes that are partly upgraded.
It increments `wdf_event_upgraded_total` for an upgrade.
Whether replay calls it or leaves the upgrade to the read path of admission is the broker epic's call, and `read_event` gives the same result either way.
The window assumes that a replayed dead letter is upgraded or read by a reader that still knows its major.

Each error carries a `dlq_reason` for the broker dead letter queue.

| `reason` | Error | `dlq_reason` |
| --- | --- | --- |
| `unsupported-major`, `retired-major`, `no-upgrade` | `UnsupportedMajor` | `UNSUPPORTED_VERSION` |
| `event-too-large` | `EventTooLarge` | `MALFORMED` |
| every other reason, including `malformed-json`, `bad-data` and `upgrade-changed-identity` | `SchemaInvalid` | `MALFORMED` |

`read_event` takes a `Registry` (schema directory, introduction dates, upgrades) so that a test can read a test-only type.
The default `CATALOGUE` is the committed contract.

## The window

There is no side-by-side emission of two majors, so a previous major has no producer once the current one is introduced.
It stays readable for `readable_days()` days from that date, which is the dead letter retention plus the maximum job duration.
A message can be dead lettered on the last day, replayed on the last day of its retention and then start a job that runs for the maximum duration.

| Constant (`window.py`) | Value | State |
| --- | --- | --- |
| `MAX_JOB_DURATION_DAYS` | 7 | placeholder |
| `DEAD_LETTER_RETENTION_DAYS` | 14 | placeholder |

The window is 21 days at the placeholders.
`INTRODUCED` in `wdf_events.models` records the date each major was introduced, and `check_catalogue` fails a catalogue that introduces a major no more than a window after the previous one, so a reader never meets a major two steps back.

## Golden and poison vectors

`contracts/events/vectors/golden/<type>/<major>.<minor>.json` holds one golden event per type and minor, in canonical bytes, built by `wdf_events.build_event` from the facts of the contract's valid vector of that type.
`golden/MANIFEST.sha256` has one `<sha256>  <path>` line per golden file, and `check_manifest` fails on a file without a line, a line without a file and a hash that differs.
A change to a golden file or its line is a contract-change PR.

`contracts/events/vectors/poison/<case>.json` holds one input per failure, and `<case>.expect.json` names the typed error and `reason` that `read_event` must raise for it.
The cases are a wrong major, schema-invalid data, a missing `time`, an `id` with `#`, bytes that are not JSON, truncated JSON, a `datacontenttype` other than `application/json` and an event over 64 KiB.

`python -m wdf_compat.vectors` rewrites every one of these files, and `--check` fails when a committed file differs from what the builders write.
