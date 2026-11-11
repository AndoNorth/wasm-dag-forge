"""Where a task runs: the placer interface, the static placer over pool table v0 and the `place`
local activity that records its answer in the workflow history."""

import json
from functools import cache
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, RootModel, StrictBool
from temporalio import activity

from .contract import naming, pools
from .models import CONTRACT

ACTIVITY = "place"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlacementRequest(_Model):
    """What a placer reads of a plan task: its type, its resources and its run timeout."""

    task_type: str = Field(min_length=1)
    cpu_milli: PositiveInt
    memory_mib: PositiveInt
    run_timeout_s: PositiveInt


class Placement(_Model):
    """The pool queue of a task, its memory class and the timeouts of its activity."""

    kind: Literal["placement"] = "placement"
    queue: str = Field(min_length=1)
    memory_class: str = Field(min_length=1)
    escalated: StrictBool
    schedule_to_start_s: PositiveInt
    start_to_close_s: PositiveInt
    heartbeat_s: PositiveInt


class Unschedulable(_Model):
    """A task that fits no pool, whose class is a placement row of the failure class table."""

    kind: Literal["unschedulable"] = "unschedulable"
    failure: Literal["insufficient_memory", "insufficient_cpu", "no_matching_pool"]
    reason: str = Field(min_length=1, max_length=1024)


class PlaceResult(RootModel[Annotated[Placement | Unschedulable, Field(discriminator="kind")]]):
    """The answer of the `place` local activity, one payload whichever verdict it carries."""


class Placer(Protocol):
    def place(self, request: PlacementRequest, escalate: bool = False) -> Placement | Unschedulable:
        """The placement of a task, or why it can never run.

        With `escalate`, a placer that has a larger memory class for the task answers with it and
        `escalated` set, and otherwise answers as without.
        """
        ...


@cache
def task_pools() -> dict[str, str]:
    """The pool of each task type of the registry."""
    types = json.loads((CONTRACT / "registry.json").read_text())["task_types"]
    return {name: entry["pool"] for name, entry in types.items()}


class StaticPlacer:
    """Places each task on the queue of its registry pool with the timeouts of pool table v0.

    The table has one memory class per pool, so an escalation is never offered.
    """

    def __init__(self):
        self.table = pools()
        self.queues = naming().pools

    def place(self, request: PlacementRequest, escalate: bool = False) -> Placement | Unschedulable:
        pool = task_pools().get(request.task_type)
        if pool not in self.table.pools:
            return Unschedulable(
                failure="no_matching_pool", reason=f"no pool runs {request.task_type} tasks"
            )
        slot = self.table.pools[pool]
        if request.memory_mib > slot.memory_mib:
            return Unschedulable(
                failure="insufficient_memory",
                reason=f"{request.memory_mib} MiB is above the {slot.memory_mib} MiB {pool} slot",
            )
        if request.cpu_milli > slot.cpu_milli:
            return Unschedulable(
                failure="insufficient_cpu",
                reason=f"{request.cpu_milli}m is above the {slot.cpu_milli}m {pool} slot",
            )
        return Placement(
            queue=self.queues[pool],
            memory_class=f"{slot.memory_mib}Mi",
            escalated=False,
            schedule_to_start_s=slot.schedule_to_start_s,
            start_to_close_s=request.run_timeout_s + slot.start_to_close_margin_s,
            heartbeat_s=slot.heartbeat_s,
        )


def place_activity(placer: Placer):
    """The `place` local activity over a placer, to register on the workflow worker."""

    @activity.defn(name=ACTIVITY)
    async def place(request: PlacementRequest, escalate: bool) -> PlaceResult:
        return PlaceResult(placer.place(request, escalate))

    return place
