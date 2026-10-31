"""The plan and its stats: the validated, defaulted and ordered form of a document.

The plan library builds them and the workflow, admission and placement read them.
A plan task holds every resolved value, so a plan is read without the profile or the slots file.
The `params` of a template plan may hold placeholders, and the `params` of a bound plan hold the
values of one job.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .models import (
    AggregateParams,
    CompileParams,
    InputRef,
    SimulateParams,
    TaskId,
)
from .registry import PARAMETER_TYPES

PLAN_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _PlanTask(_Model):
    id: TaskId
    depends_on: list[TaskId]
    inputs: list[InputRef]
    cpu_milli: StrictInt = Field(ge=1)
    memory_mib: StrictInt = Field(ge=1)
    run_timeout_s: StrictInt = Field(ge=1)
    retries: StrictInt = Field(ge=0)
    checkpoint: StrictBool
    allow_partial: StrictBool


class CompilePlanTask(_PlanTask):
    type: Literal["compile"]
    pool: Literal["compile"]
    params: CompileParams


class SimulatePlanTask(_PlanTask):
    type: Literal["simulate"]
    pool: Literal["simulate"]
    params: SimulateParams


class AggregatePlanTask(_PlanTask):
    type: Literal["aggregate"]
    pool: Literal["aggregate"]
    params: AggregateParams


PlanTask = Annotated[
    CompilePlanTask | SimulatePlanTask | AggregatePlanTask, Field(discriminator="type")
]


class Plan(_Model):
    """Tasks in topological order, ties broken by task id."""

    plan_version: Literal[1]
    # The name of the limits profile the plan was compiled under.
    profile: str = Field(pattern="^[a-z]+$")
    max_parallel: StrictInt = Field(ge=1)
    parameters: dict[str, Literal[*PARAMETER_TYPES]]
    tasks: list[PlanTask]


class TypeCounts(_Model):
    compile: StrictInt = Field(ge=0)
    simulate: StrictInt = Field(ge=0)
    aggregate: StrictInt = Field(ge=0)


class PlanStats(_Model):
    """What admission and placement read from the job row instead of parsing the plan."""

    task_count: StrictInt = Field(ge=0)
    counts: TypeCounts
    # The number of compile tasks, each with the chain of simulate tasks that name its component.
    chain_count: StrictInt = Field(ge=0)
    # The widest topological level, a plain bound that is not the exact antichain.
    max_width: StrictInt = Field(ge=0)
    # A bound along one path of run timeouts and retries, not a wall-clock bound.
    critical_path_timeout_bound_s: StrictInt = Field(ge=0)
    peak_cpu_milli: StrictInt = Field(ge=0)
    peak_memory_mib: StrictInt = Field(ge=0)
    largest_cpu_milli: StrictInt = Field(ge=0)
    largest_memory_mib: StrictInt = Field(ge=0)
    checkpoint_tasks: StrictInt = Field(ge=0)
    estimated_history_events: StrictInt = Field(ge=0)
