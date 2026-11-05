"""Checkpoint objects: a data object and checkpoint.json, written last."""

from typing import Any

from ._stub import not_implemented

STUBBED = ("Verified", "write_checkpoint", "verify_checkpoint", "prune_plan")


class Verified:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("K")


def write_checkpoint(
    client: Any,
    source: Any,
    producer: str,
    tenant: str,
    batch: str,
    seed: str,
    segment: str,
    meta: dict[str, Any],
    deadline: float | None = None,
    progress: Any = None,
) -> Any:
    raise not_implemented("K")


def verify_checkpoint(client: Any, reference: Any, deadline: float | None = None) -> Verified:
    raise not_implemented("K")


def prune_plan(references: list[Any], keep: int) -> list[Any]:
    raise not_implemented("K")
