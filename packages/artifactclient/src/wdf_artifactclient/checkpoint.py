"""Checkpoint objects: a data object and checkpoint.json, written last.

A checkpoint exists only once its checkpoint.json does, so an interrupted write is invisible.
The reference of a checkpoint is the reference of its checkpoint.json.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from wdf_events.models import CheckpointReference, ProducerRef

from .client import ArtifactClient, Deadline, Progress, Source, verify
from .errors import ArtifactLost, CheckpointCorrupt
from .keys import split_uri

STUBBED = ()

DATA_NAME = "data.bin"
METADATA_NAME = "checkpoint.json"
# The metadata lists a handful of references, so a larger object is not one.
METADATA_CAP = 1024 * 1024
# The fewest checkpoints that a prune leaves, as the retention contract requires of its keep count.
MIN_KEEP = 2


class Header(BaseModel):
    """What the caller states of a checkpoint. The format version and the progress are opaque."""

    model_config = ConfigDict(extra="forbid")

    format_version: Any
    progress: Any
    wasm_sha256: str = Field(pattern="^[0-9a-f]{64}$")


class Metadata(Header):
    """The content of checkpoint.json: the header and the references of the data objects."""

    objects: list[CheckpointReference] = Field(min_length=1)


@dataclass(frozen=True)
class Verified:
    """A checkpoint whose metadata and data objects all matched their references."""

    reference: CheckpointReference
    format_version: Any
    progress: Any
    wasm_sha256: str
    objects: tuple[CheckpointReference, ...]


def as_checkpoint(reference: Any) -> CheckpointReference:
    data = reference.model_dump() if isinstance(reference, BaseModel) else reference
    return CheckpointReference.model_validate(data)


def write_checkpoint(
    client: ArtifactClient,
    source: Source,
    producer: ProducerRef,
    tenant: str,
    batch: str,
    seed: int | str,
    segment: str,
    meta: dict[str, Any],
    deadline: float | None = None,
    progress: Progress | None = None,
) -> CheckpointReference:
    """Stores the data object, then checkpoint.json, and returns the reference of the latter.

    `meta` holds exactly `format_version`, `progress` and `wasm_sha256`.
    """
    header = Header.model_validate(meta)
    limit = Deadline(client.retry, deadline)

    def put(body: Source, name: str):
        limit.check()
        left = limit.remaining()
        return client.put_attempt(
            body,
            "checkpoint",
            name,
            producer,
            tenant,
            batch,
            seed,
            segment,
            deadline=None if left == float("inf") else left,
            progress=progress,
        )

    data = as_checkpoint(put(source, DATA_NAME))
    document = Metadata(**header.model_dump(), objects=[data])
    body = json.dumps(document.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return as_checkpoint(put(body.encode(), METADATA_NAME))


def verify_checkpoint(
    client: ArtifactClient, reference: Any, deadline: float | None = None
) -> Verified:
    """Reads the metadata, then each data object through the hash check.

    A mismatch or a missing object is `CheckpointCorrupt`, while a store fault is raised as it is
    (`StoreUnavailable` after the retries of the client), so an outage never condemns a checkpoint.
    """
    ref = as_checkpoint(reference)
    if ref.name != METADATA_NAME:
        raise ValueError(f"{ref.name} is not the {METADATA_NAME} of a checkpoint")
    limit = Deadline(client.retry, deadline)

    def left() -> float | None:
        limit.check()
        remaining = limit.remaining()
        return None if remaining == float("inf") else remaining

    try:
        if ref.size > METADATA_CAP:
            raise CheckpointCorrupt(f"{ref.uri} holds {ref.size} bytes, above {METADATA_CAP}")
        raw = client.get_bytes(ref, METADATA_CAP, deadline=left())
        try:
            document = Metadata.model_validate(json.loads(raw))
        except (ValueError, ValidationError) as error:
            raise CheckpointCorrupt(f"{ref.uri} is not checkpoint metadata: {error}") from error
        folder = split_uri(ref.uri)[1].rpartition("/")[0] + "/"
        for data in document.objects:
            if not split_uri(data.uri)[1].startswith(folder):
                raise CheckpointCorrupt(f"{data.uri} is not under the segment of {ref.uri}")
            verify(client, data, left())
    except ArtifactLost as lost:
        raise CheckpointCorrupt(f"{lost}") from lost
    return Verified(
        ref,
        document.format_version,
        document.progress,
        document.wasm_sha256,
        tuple(document.objects),
    )


def prune_plan(references: Sequence[Any], keep: int) -> list[Any]:
    """The checkpoints to delete from a list that is newest first, as the index returns it.

    At least two are kept whatever `keep` says. Each reference is a checkpoint.json whose data
    objects share its key prefix.
    """
    return list(references[max(keep, MIN_KEEP) :])
