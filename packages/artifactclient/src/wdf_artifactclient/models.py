"""The models of the artifact contract files, the source of their generated schemas."""

from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

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


# Each contract file with its model and the schema generated from it.
FILES = {
    "failures": (Failures, "contracts/artifact/failures.schema.json"),
    "staging": (Staging, "contracts/artifact/staging.schema.json"),
}
