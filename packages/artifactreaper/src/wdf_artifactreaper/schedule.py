"""The Temporal Schedule that runs a reaper pass, and the worker that serves it."""

from typing import Any

from ._stub import not_implemented

SCHEDULE_ID = "wdf-artifact-reaper"
TASK_QUEUE = "wdf-artifact-reaper"

STUBBED = ("ensure", "trigger", "worker")


def ensure(temporal_address: str) -> None:
    raise not_implemented("P")


def trigger(temporal_address: str) -> Any:
    raise not_implemented("P")


def worker(temporal_address: str) -> None:
    raise not_implemented("P")
