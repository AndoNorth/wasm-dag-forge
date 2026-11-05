# wdf-artifactclient

The artifact client library of the artifact passing epic, and the loader of the artifact contract files.
The contract it loads is pinned in `contracts/artifact/README.md`.

## State

The contract loader, the models, the schema generator, the key builders, the typed errors, the client, `verify`, commit by reference and staging are delivered.
The modules of the checkpoints, the aggregate index, the compile cache and the fault hooks are stubs.
A stub raises `NotImplementedError("not implemented: owned by lane <letter>")`.
Each module lists its stubs in `STUBBED`, and the lane that delivers a name removes it from that list.
`tests/test_stubs.py` accepts the stub error only for the names of `STUBBED`.

## The client

`ArtifactClient(endpoint, credentials, retry, chunk_bytes)` holds the credentials of one store role.
It is synchronous and thread safe, and it never heartbeats.
Every call takes `deadline`, the seconds that the call may take from its start, and `progress`, which receives the number of bytes moved since its last call.

- `put_content` stores bytes under the key of their own hash.
  A path is hashed first and a stream is spooled to a scratch file while it is hashed, so a retry can read it again.
  The put carries `If-None-Match: *`, and an object that exists is a success.
- `put_attempt` stores bytes under the key of an attempt, which `keys.attempt_key` builds from the producer, tenant, batch, seed and segment that the caller names.
  The hash is taken while the bytes stream.
- Both send one request up to 8 MiB and a multipart upload above it, in parts of 8 MiB.
  Any failure of a multipart upload aborts it, so no incomplete upload stays behind.
- `get` writes a temporary file beside the destination and renames it only when the size and the SHA-256 match the reference.
  `get_bytes` does the same into memory for objects up to a limit, `head` reads the stored size, and `verify` reads an object through the hash check and keeps nothing.
- A missing or corrupt object is fetched once more, and the second failure is `ArtifactLost` carrying the first as its `cause`.
- `put_content` with `conn` and `job_id` is the caller that holds a database connection, which is the dispatch API under the `wdf_artifacts` role.
  It indexes the row first and revives a soft deleted or purged one, then sends the bytes, then links the job, so a revived row is never left without its object.
  If a purge lands between the steps, the link finds the row purged and the three steps run again, up to three rounds, and then `OutputUploadFailed` is raised.
  `conn` and `job_id` come together, and either one alone is a `ValueError`.
  The rows follow the transaction of the caller, and a connection in autocommit makes each step durable as it goes.

A source above the size cap of its kind in `contracts/artifact/staging.yaml` raises `TooLarge`, and a path whose bytes change during the upload raises `SourceChanged`, before the object can take the wrong address.
Both are `ValueError`, because the failure taxonomy has no code for a caller's own mistake.

## Commit by reference

`commit(conn, client, references, job_id, task, accepted_attempt=None)` is what the workflow's record activity calls for an accepted attempt, with a client of the `workflow` store role and a connection of the `wdf_artifacts` role.
A worker only returns references, and the commit is the one place where they become rows of the index.

- An attempt-scoped reference is refused with `CommitRefused` unless it names this job, task and accepted attempt, and nothing is read or written first.
  A zombie attempt that uploads late writes only under its own attempt prefix, which no row names.
- Each reference takes its row lock, indexing the row if it is absent, then the object is checked with a `HEAD`, and a content-addressed reference is linked to the job.
  A tombstoned or purged row is made live only after its object was seen, and `Committed.revived` lists those rows.
- An object that is gone, or whose size differs, raises `OutputUploadFailed` and rolls back every change of the call, so the workflow retries the attempt, which uploads the bytes again.
- Rows are taken in uri order, so two commits never wait on each other in a cycle.

The call runs in one transaction block of the connection and never commits an outer transaction of the caller.

## Staging

`stage_inputs(client, inputs, scratch)` downloads each input reference to `input/<reference name>` through the hash check and makes the file read only.
A name that repeats, that is `input.json` or that has a slash is refused before any download.
The library never writes `input.json`, because its content belongs to the caller.
`collect_outputs(client, scratch, task_type, producer, tenant, batch, seed)` uploads the declared outputs of `staging.yaml` and returns `Collected(references, undeclared)`.
A content-addressed kind goes up by its hash and an attempt-scoped kind under the key of the producer, and the seed reaches only a key that has one.
A declared output that is absent or not a regular file raises `OutputMissing` before anything is uploaded, so a failed attempt leaves no objects behind.
An undeclared regular file, a symbolic link and a directory are not uploaded and come back in `undeclared`, never as a failure.
`capture_tail(stream, cap)` keeps the last `cap` bytes of a stream in a bounded buffer, and `upload_tail` stores them as `log-tail` under the role of the task type, with the undeclared entries listed at the end, within the cap.
The tail is uploaded for a failed attempt as for a successful one.

## Retries

`RetryPolicy` holds every number: 4 tries, a delay that starts at 0.5 s and doubles, and random jitter of up to half the delay.
The sleeps are cut to the time that the deadline leaves, so the total sleep never goes beyond it.
A missing object, a refused credential and a corrupt object are not retried, and every other store fault is.
After the last try a put that the store answered with a 500 raises `OutputUploadFailed`, which is how a full store surfaces, and any other fault raises `StoreUnavailable`.
The numbers are estimates and a change touches only `RetryPolicy`.

## Per product facts

These are recorded by `tests/test_client.py::test_product_facts_if_none_match_and_the_md5_entity_tag`, which prints them, and the client works whichever way they fall, because the consumer check is the authority.

| Product | `If-None-Match: *` on a put of an object that exists | `If-None-Match: *` on the completion of a multipart upload | Entity tag of a single part put |
| --- | --- | --- | --- |
| SeaweedFS | honoured, refused with 412 | honoured, refused with 412 | the MD5 of the bytes |
| RustFS | honoured, refused with 412 | honoured, refused with 412 | the MD5 of the bytes |

The client compares an entity tag that has the shape of an MD5 with the MD5 taken while streaming, and ignores any other tag.

## Testing helpers

`wdf_artifactclient.testing` is a pytest plugin, registered by the package entry point.
It offers a stand-in guest runner and the fixtures `scratch` and `runner`.
A guest is a callable that reads `input/` and writes `output/`.
A real runner joins the staging tests by adding itself to `RUNNERS`.

## Commands

```sh
nix develop -c uv run --frozen python -m wdf_artifactclient.contract --check contracts/artifact/failures.yaml
nix develop -c uv run --frozen python -m wdf_artifactclient.gen --check
nix develop -c uv run --frozen python -m wdf_artifactclient verify --endpoint URL --credentials FILE --role ROLE REF.json
nix develop -c uv run --frozen pytest packages/artifactclient -q -rs
nix develop -c uv run --frozen pytest packages/artifactclient -q -m isolated -k test_client
```

`verify` exits 0 for an intact object, 1 with `code: <CODE>` on stderr (and `cause: <CODE>` for a lost artifact), and 2 for a usage error.
