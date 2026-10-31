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
