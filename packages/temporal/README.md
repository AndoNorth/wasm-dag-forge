# wdf-temporal

Temporal helpers of the event contract, imported as `wdf_temporal`.
The contract itself is [contracts/events/README.md](../../contracts/events/README.md), and the event models are in `packages/events/`.

## Payload form

Payload form: `converter`

`wdf_temporal.event_data_converter` stores an event in the Temporal history as the canonical bytes of `wdf_events.to_bytes`, under the metadata encoding `json/plain`.
The Temporal UI therefore shows the event as readable JSON, and `json.loads` reads the data.
The stock `json/plain` converter cannot stand in for it, because it escapes non-ASCII text, so an event with a Unicode attribute would be stored as different bytes than the store and the broker hold.

The other payload form, `bytes`, would store the same bytes under `binary/plain`, and the UI would show base64.
It is the fallback if a later SDK or server version stops keeping the data of a `json/plain` payload byte for byte.
The replay corpus records histories in the declared form.

Any other model that crosses a workflow or activity boundary, such as the job params block, is stored as compact JSON with sorted keys.
A value that is not a model keeps the stock `json/plain` behaviour.

## Stand-in workflow

`wdf_temporal.standin.StandInJob` takes the accepted event and the job params block as its two start arguments.
It builds a task request event, runs one activity with it as input, and returns the result ready event that the activity builds.
The real workflow replaces it, and keeps the same converter.

## Trace context

`new_traceparent()` creates the W3C `traceparent` once, at submit, and every event carries it from there.
`stage_span` opens the span of a stage as a child of the span that an incoming `traceparent` names, and `current_traceparent()` gives the value for the event the stage emits.
`tracing_interceptor()` is the `temporalio.contrib.opentelemetry` interceptor, which carries the context through the workflow and activity headers.
The stand-in runner passes `opentelemetry` through the workflow sandbox, so the interceptor works inside a workflow.

The interceptor parents every workflow-side span on the span of the start call, so the stage chain is submit, admission, the start of the workflow, and the activity run.
The guarantee is one trace id across the stages of a run, and nothing is claimed about a trace that spans several days.

## Replay corpus

`corpus/<release>/<name>.json` holds one recorded Temporal history per file, with the build label that recorded it and the workflow type.
`corpus/owners.yaml` maps a workflow type to the epic that owns its histories.
The histories are recorded in the declared payload form.

Record a release from the current code, in one container started through `wdf_itest`:

```sh
uv run --frozen python -m wdf_temporal.corpus record <release>
```

`replay_corpus(classes)` replays every history whose workflow type has a class among the ones given, with the current workflow code and payload decoding.
It fails with a message that names the history file and its owner for a history that has an `owners.yaml` entry and no class given, and for a history with neither.
A history is never skipped, so a runner built from a partial list of classes cannot pass over the histories of another epic.
Another epic adds its histories under a `<release>/` directory it creates, with a line in `owners.yaml`, and replays them with its own class through the same runner.

The corpus only grows.
The one exception is the retirement of a release: a pull request that names the release directory and deletes it, after the compatibility window of `wdf_compat.window.readable_days()` days.
`test_replay_negative.py` rewrites a recorded history with a renamed event field and expects the replay to fail, so the gate is shown able to fail.

Worker versioning on activity queues other than the workflow queue is not proven here, as the epic says.
