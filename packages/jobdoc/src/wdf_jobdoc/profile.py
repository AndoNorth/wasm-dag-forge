"""The limits profile: numbers per profile name, and the pool table read from a slots file.

Nothing here reads a file, so the models stay usable inside a workflow.
`profile_files.load_profile` is the one place that reads them.
"""

import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

POOLS = ("compile", "simulate", "aggregate")
PROFILES = ("ci", "dev", "soak", "permissive")


class SlotsIncomplete(Exception):
    """A slots file has no entry for a pool, and no stand-in budget is invented for it."""

    def __init__(self, pool: str):
        self.pool = pool
        super().__init__(f"the slots file has no entry for the {pool} pool")


class Slot(BaseModel):
    """What one task may use: one task runs in one pod, so a pool slot is a pod's limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_milli: int = Field(ge=1)
    memory_mib: int = Field(ge=1)


class PoolTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    compile: Slot
    simulate: Slot
    aggregate: Slot

    def slot(self, task_type: str) -> Slot:
        return getattr(self, task_type)


def parse_cpu_milli(quantity: str) -> int:
    """Millicores of a Kubernetes cpu quantity: whole or decimal cores, or millicores with `m`."""
    text = str(quantity)
    if re.fullmatch(r"[0-9]+m", text):
        return int(text[:-1])
    if re.fullmatch(r"[0-9]+(\.[0-9]{1,3})?", text):
        return int(Decimal(text) * 1000)
    raise ValueError(f"not a cpu quantity of cores or millicores: {quantity!r}")


def parse_memory_mib(quantity: str) -> int:
    """MiB of a memory quantity written in Mi or Gi."""
    match = re.fullmatch(r"([0-9]+)(Mi|Gi)", str(quantity))
    if not match:
        raise ValueError(f"not a memory quantity in Mi or Gi: {quantity!r}")
    return int(match[1]) * (1024 if match[2] == "Gi" else 1)


def pool_table(slots: dict) -> PoolTable:
    """The pool table of a parsed slots file, whose budget is the limits of each pool.

    Raises SlotsIncomplete naming the first pool without an entry.
    """
    pools = slots.get("pools") or {}
    entries = {}
    for pool in POOLS:
        if pool not in pools:
            raise SlotsIncomplete(pool)
        limits = pools[pool]["limits"]
        entries[pool] = Slot(
            cpu_milli=parse_cpu_milli(limits["cpu"]), memory_mib=parse_memory_mib(limits["memory"])
        )
    return PoolTable(**entries)


class Limits(BaseModel):
    """The numbers of one profile, as limits.yaml holds them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tasks: int = Field(ge=1)
    max_parallel: int = Field(ge=1)
    default_max_parallel: int = Field(ge=1)
    max_document_bytes: int = Field(ge=1)
    max_plan_bytes: int = Field(ge=1)
    max_retries: int = Field(ge=0)
    max_run_timeout_s: int = Field(ge=1)
    # Matches wdf_compat.window.MAX_JOB_DURATION_DAYS, which a test keeps equal for the ci profile.
    max_job_duration_days: int = Field(ge=1)
    max_history_events: int = Field(ge=1)
    history_events_per_job: int = Field(ge=0)
    history_events_per_task: int = Field(ge=0)
    history_events_per_retry: int = Field(ge=0)
    compile_run_timeout_s: int = Field(ge=1)
    compile_retries: int = Field(ge=0)
    classes: list[Literal["short", "long"]] = Field(min_length=2, max_length=2)
    class_threshold_s: int = Field(ge=1)
    # A ceiling that replaces the slot of every pool when set, so a failure test can ask for more.
    ceiling_cpu_milli: int | None = Field(default=None, ge=1)
    ceiling_memory_mib: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _default_parallelism_fits(self):
        if self.default_max_parallel > self.max_parallel:
            raise ValueError("default_max_parallel is above max_parallel")
        return self


ProfileName = Annotated[str, StringConstraints(pattern="^[a-z]+$")]


class LimitsFile(BaseModel):
    """limits.yaml: the numbers of every profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profiles: dict[ProfileName, Limits]

    @model_validator(mode="after")
    def _every_profile_is_present(self):
        if set(self.profiles) != set(PROFILES):
            raise ValueError(f"the profiles are exactly {', '.join(PROFILES)}")
        return self


class LimitsProfile(Limits):
    """A profile with its name and the pool table of its slots file."""

    name: str
    pools: PoolTable

    @property
    def critical_path_limit_s(self) -> int:
        return self.max_job_duration_days * 86400
