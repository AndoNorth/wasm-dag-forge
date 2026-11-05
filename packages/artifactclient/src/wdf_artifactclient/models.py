"""The models of the artifact contract files, the source of their generated schemas."""

from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, RootModel
from wdf_events.models import UUID_PATTERN, AttemptReference

# The contract files the runtime reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"

# How a caller of the client treats the failure before it surfaces as an attempt failure.
RETRY_RULES = (
    "backoff-within-deadline",
    "refetch-once",
    "fallback-to-previous-checkpoint",
    "none",
)
ATTEMPT_CLASSES = ("retryable", "non-retryable")

RetryRule = Literal[RETRY_RULES]
AttemptClass = Literal[ATTEMPT_CLASSES]


class Surface(BaseModel):
    """What a failure looks like to a caller: its exception and the start of the event reason."""

    model_config = ConfigDict(extra="forbid")

    exception: str = Field(pattern="^[A-Z][A-Za-z]*$")
    event_reason: str = Field(
        pattern="^[A-Z][A-Z_]*: $",
        description="The start of the reason of a task.attempt.failed event, the code and a colon.",
    )


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern="^[A-Z][A-Z_]*$")
    cause: str = Field(min_length=1)
    retry: RetryRule
    attempt_class: AttemptClass
    escalates_to: str | None = Field(
        default=None, description="The code that this failure becomes once its retry rule is spent."
    )
    surface: Surface


class Failures(BaseModel):
    """failures.yaml: every artifact failure with its cause, retry rule, class and surface."""

    model_config = ConfigDict(extra="forbid")

    codes: list[Failure] = Field(min_length=1)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]*$")
    kind: str
    cap_bytes: PositiveInt


class TaskType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outputs: list[Output]


class Directories(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(pattern="^[a-z]+$")
    output: str = Field(pattern="^[a-z]+$")


class LogTail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]*$")
    cap_bytes: PositiveInt


class Staging(BaseModel):
    """staging.yaml: the scratch directory of a task, its declared outputs and the size caps."""

    model_config = ConfigDict(extra="forbid")

    directories: Directories
    input_file: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]*$")
    undeclared: Literal["skip-and-list"]
    non_regular: Literal["skip-and-list"]
    missing_output: str = Field(pattern="^[A-Z][A-Z_]*$", description="A code of failures.yaml.")
    log_tail: LogTail
    kind_caps: dict[str, PositiveInt]
    task_types: dict[str, TaskType]


class Window(BaseModel):
    """How long the reaper keeps the objects of a job after its terminal_at, in days."""

    model_config = ConfigDict(extra="forbid")

    results_days: PositiveInt
    checkpoints_days: PositiveInt


class ContentRules(BaseModel):
    """When a content-addressed blob is collected, which no job window governs."""

    model_config = ConfigDict(extra="forbid")

    unreferenced_grace_days: PositiveInt = Field(
        description="How long a row stays unreferenced before it is tombstoned."
    )
    tombstone_interval_hours: PositiveInt = Field(
        description="How long a row stays tombstoned before its object is deleted."
    )
    orphan_grace_days: PositiveInt = Field(
        description="How old an object without a live row is before the orphan sweep deletes it."
    )


class Retention(BaseModel):
    """retention.yaml: the windows of the artifact reaper, all below the safety net of the store."""

    model_config = ConfigDict(extra="forbid")

    statuses: dict[str, Window] = Field(
        description="A window for each terminal status. A status that is not terminal has none."
    )
    checkpoint_keep: int = Field(
        ge=1, description="How many of the newest checkpoints of a running job survive a prune."
    )
    cache_unused_days: PositiveInt = Field(
        description="How long a compile cache entry may stay unused before it expires."
    )
    content: ContentRules


STATUSES = ("succeeded", "failed", "missing")
PARTIAL = ("refuse", "allow")
TASK_ID = "^[a-z0-9][a-z0-9._-]{0,62}$"


class IndexHeader(BaseModel):
    """Line 1 of an aggregate index object."""

    model_config = ConfigDict(extra="forbid")

    schema_: Literal["wdf.artifact.index/1"] = Field(alias="schema")
    job: str = Field(pattern=UUID_PATTERN, description="The job that the index feeds.")
    tenant: str = Field(pattern="^[^/]+$")
    task: str = Field(pattern=TASK_ID, description="The aggregate task that reads the index.")
    partial: Literal[PARTIAL] = Field(
        description="Whether the reader may accept an entry that did not succeed."
    )


class IndexEntry(BaseModel):
    """One input of the aggregate task, in task id order."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(pattern=TASK_ID, description="The task id of the input.")
    status: Literal[STATUSES]
    reference: AttemptReference | None = Field(
        default=None,
        description="The result reference, present exactly when the status is succeeded.",
    )


class IndexLine(RootModel[IndexHeader | IndexEntry]):
    """A line of an aggregate index object, which is a header on line 1 and an entry after it."""


# Each contract file with its model and the schema generated from it.
FILES = {
    "failures": (Failures, "contracts/artifact/failures.schema.json"),
    "staging": (Staging, "contracts/artifact/staging.schema.json"),
    "retention": (Retention, "contracts/artifact/retention.schema.json"),
    "index": (IndexLine, "contracts/artifact/index.schema.json"),
}
