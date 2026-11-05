"""Object keys built from layout.yaml, refusing what the layout would refuse."""

import re

from wdf_events import layout
from wdf_events.models import ProducerRef

STUBBED = ()
CONTENT = "content-addressed"
HASH = re.compile("^[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"<([a-z0-9]+)>")
# Segments that carry no structure of their own: a tenant, a batch, a seed, a checkpoint segment.
FREE_SEGMENT = re.compile(r"^[^/\x00-\x1f]+$")
NAME = re.compile(r"^[^/\x00-\x1f]{1,255}$")


class InvalidKey(ValueError):
    """A key that the layout refuses, or a kind that has no key of the requested class."""


def kind_spec(kind: str) -> dict:
    kinds = layout()["kinds"]
    if kind not in kinds:
        raise InvalidKey(f"{kind} is not a kind of the layout")
    return kinds[kind]


def bucket_of(kind: str) -> str:
    return kind_spec(kind)["bucket"]


def uri_of(kind: str, key: str) -> str:
    return f"s3://{bucket_of(kind)}/{key}"


def split_uri(uri: str) -> tuple[str, str]:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def render(template: str, values: dict[str, str]) -> str:
    """Fills each placeholder of a template, and refuses a missing value or an extra one."""
    wanted = set(PLACEHOLDER.findall(template))
    if set(values) != wanted:
        missing, extra = sorted(wanted - set(values)), sorted(set(values) - wanted)
        raise InvalidKey(f"template {template}: missing {missing}, not in the template {extra}")
    return PLACEHOLDER.sub(lambda m: values[m[1]], template)


def free_segment(label: str, value: object) -> str:
    text = str(value)
    if not FREE_SEGMENT.fullmatch(text) or text in (".", ".."):
        raise InvalidKey(f"{label} {text!r} is not a key segment")
    return text


def content_key(kind: str, sha256: str) -> str:
    """The key of a content-addressed kind, which is derived from the hash of the bytes."""
    spec = kind_spec(kind)
    if spec["class"] != CONTENT:
        raise InvalidKey(f"{kind} is not content-addressed")
    if not HASH.fullmatch(sha256):
        raise InvalidKey(f"{sha256!r} is not a lowercase hex SHA-256")
    return render(spec["key"], {"hash2": sha256[:2], "hash": sha256})


def attempt_key(
    kind: str,
    name: str,
    producer: ProducerRef | dict,
    tenant: str,
    batch: str,
    seed: int | str | None = None,
    segment: str | None = None,
) -> str:
    """The key of an attempt-scoped kind.

    Every segment comes from the caller and is only checked: the attempt is never inferred.
    A seed or a segment is required exactly when the template of the kind has one.
    """
    spec = kind_spec(kind)
    if spec["class"] == CONTENT:
        raise InvalidKey(f"{kind} is content-addressed")
    if not NAME.fullmatch(name) or name in (".", ".."):
        raise InvalidKey(f"name {name!r} is not an object name")
    try:
        who = (
            producer if isinstance(producer, ProducerRef) else ProducerRef.model_validate(producer)
        )
    except ValueError as error:
        raise InvalidKey(f"producer: {error}") from error
    offered = {
        "tenant": tenant,
        "batch": batch,
        "job": who.job,
        "seed": seed,
        "task": who.task,
        "attempt": who.attempt,
        "segment": segment,
        "name": name,
    }
    wanted = set(PLACEHOLDER.findall(spec["key"]))
    for label in ("seed", "segment"):
        if (offered[label] is None) == (label in wanted):
            raise InvalidKey(f"{kind} {'needs' if label in wanted else 'takes no'} a {label}")
    values = {
        label: free_segment(label, value)
        if label in ("tenant", "batch", "seed", "segment")
        else value
        for label, value in offered.items()
        if label in wanted
    }
    return render(spec["key"], values)
