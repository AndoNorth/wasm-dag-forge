"""Validation of an artifact reference before it is stored.

`wdf_events.models` checks the shape and the producer rule. The layout checks that follow are the
ones of contracts/artifact/check.sh: the bucket of the kind and the key template of its class.
"""

import re

from pydantic import BaseModel, TypeAdapter, ValidationError
from wdf_events.models import ArtifactReference

from .models import LAYOUT

_ADAPTER = TypeAdapter(ArtifactReference)
_PLACEHOLDER = re.compile(r"<(job|task|attempt|name)>")


class InvalidReference(ValueError):
    """A refused reference; the message starts with the reason the loader of the contract gives."""


def split_uri(uri: str) -> tuple[str, str]:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def _check_layout(ref) -> None:
    spec = LAYOUT["kinds"][ref.kind]
    bucket, key = split_uri(ref.uri)
    if bucket != spec["bucket"]:
        raise InvalidReference(
            f"wrong-bucket: kind {ref.kind} lives in {spec['bucket']}, the uri names {bucket}"
        )
    segments, template = key.split("/"), spec["key"].split("/")
    mismatch = InvalidReference(f"key-template-mismatch: {key} does not match {spec['key']}")
    if spec["class"] == "content-addressed":
        if len(segments) != len(template) or segments[0] != "sha256":
            raise mismatch
        if segments[1] != ref.sha256[:2]:
            raise InvalidReference(f"bad-hash-prefix: {segments[1]} is not the start of the hash")
        if segments[2] != ref.sha256:
            raise InvalidReference(
                f"hash-mismatch: {segments[2]} is not the sha256 of the reference"
            )
        return
    producer = ref.producer
    if len(segments) != len(template):
        if producer.attempt in segments:
            raise mismatch
        raise InvalidReference(f"missing-attempt-segment: {key} has no segment for the attempt")
    expected = {
        "job": producer.job,
        "task": producer.task,
        "attempt": producer.attempt,
        "name": ref.name,
    }
    for part, segment in zip(template, segments, strict=True):
        placeholder = _PLACEHOLDER.fullmatch(part)
        if not segment or (placeholder and segment != expected[placeholder[1]]):
            raise mismatch
        if not part.startswith("<") and segment != part:
            raise mismatch


def validate(reference) -> ArtifactReference:
    """The typed reference, or InvalidReference naming the first rule that it breaks."""
    data = reference.model_dump() if isinstance(reference, BaseModel) else reference
    try:
        ref = _ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise InvalidReference(f"schema-violation: {exc}") from exc
    _check_layout(ref)
    return ref
