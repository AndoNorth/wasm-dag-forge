"""Stubs and fakes that stand in for the placement service, the pool workers and the control
activities, with the conformance tests that the real ones pass."""

import asyncio

from temporalio.client import Client
from wdf_temporal import event_data_converter

from ..contract import naming
from .control import FakeControl, control_activity_contract
from .placer import placer_contract
from .pool import (
    ContractCases,
    Die,
    Fail,
    FakePool,
    Hang,
    Interruptible,
    Succeed,
    Zombie,
    activity_contract,
    run_task_once,
)

__all__ = [
    "ContractCases",
    "Die",
    "Fail",
    "FakeControl",
    "FakePool",
    "Hang",
    "Interruptible",
    "Succeed",
    "Zombie",
    "activity_contract",
    "connect",
    "control_activity_contract",
    "placer_contract",
    "run_task_once",
]


async def connect(address: str, attempts: int = 120) -> Client:
    """A client of the namespace of naming.yaml with the payload converter of wdf_temporal, once
    the server answers."""
    for _ in range(attempts - 1):
        try:
            return await Client.connect(
                address, namespace=naming().namespace, data_converter=event_data_converter
            )
        except RuntimeError:
            await asyncio.sleep(0.5)
    return await Client.connect(
        address, namespace=naming().namespace, data_converter=event_data_converter
    )
