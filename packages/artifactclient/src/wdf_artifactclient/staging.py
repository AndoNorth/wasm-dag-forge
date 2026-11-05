"""Task I/O staging: inputs into a scratch directory, declared outputs out, the output tail."""

from pathlib import Path
from typing import Any, BinaryIO

from ._stub import not_implemented

STUBBED = ("Staged", "Collected", "stage_inputs", "collect_outputs", "capture_tail")


class Staged:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("C")


class Collected:
    """The references of the uploaded outputs and the undeclared entries that were not uploaded."""

    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("C")


def stage_inputs(
    client: Any,
    inputs: dict[str, Any],
    scratch: Path,
    deadline: float | None = None,
    progress: Any = None,
) -> Staged:
    raise not_implemented("C")


def collect_outputs(
    client: Any,
    scratch: Path,
    task_type: str,
    producer: str,
    tenant: str,
    batch: str,
    seed: str,
    deadline: float | None = None,
    progress: Any = None,
) -> Collected:
    raise not_implemented("C")


def capture_tail(stream: BinaryIO, cap: int) -> bytes:
    raise not_implemented("C")
