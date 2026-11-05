"""Object keys built from layout.yaml."""

from ._stub import not_implemented

STUBBED = ("content_key", "attempt_key")


def content_key(kind: str, sha256: str) -> str:
    raise not_implemented("C")


def attempt_key(
    kind: str,
    name: str,
    producer: str,
    tenant: str,
    batch: str,
    seed: str,
    segment: str | None = None,
) -> str:
    raise not_implemented("C")
