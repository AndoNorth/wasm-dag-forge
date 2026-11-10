"""The models of the activity and Temporal contracts, the source of their generated schemas."""

from importlib import resources
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StrictBool,
    StrictInt,
    StrictStr,
    field_serializer,
    model_validator,
)
from wdf_events import Event
from wdf_events.models import (
    TIME_PATTERN,
    UUID_PATTERN,
    ArtifactReference,
    AttemptReference,
    CheckpointReference,
    ManifestReference,
)
from wdf_jobdoc.models import (
    SEED_LIMIT,
    SHA256_PATTERN,
    TASK_ID_PATTERN,
    CompileParams,
    DigestListParams,
    SummaryStatsArgs,
    SummaryStatsParams,
)
from wdf_runtime.models import Input

# The contract files the workflow reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"

# Each payload's bound counts its canonical JSON bytes, as wdf_temporal.converter writes a model.
TASK_INPUT_MAX_BYTES = 262_144
TASK_RESULT_MAX_BYTES = 4096
FAILURE_DETAILS_MAX_BYTES = 4096
HEARTBEAT_MAX_BYTES = 2048
STATUS_QUERY_MAX_BYTES = 524_288

CONTROL_SEQ_MAX = 2_147_483_647
REASON_MAX = 1024
NAME = "^[a-z][a-z0-9_]{0,63}$"
QUEUE = "^[a-z][a-z0-9-]{0,127}$"

Time = Annotated[str, Field(pattern=TIME_PATTERN)]
Pool = Literal["compile", "simulate", "aggregate"]
TaskType = Pool


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LogTailReference(AttemptReference):
    """The reference of an attempt's log tail, narrowed as ResultReference narrows its kinds."""

    kind: Literal["log-tail"]


class BoundSummaryStatsArgs(SummaryStatsArgs):
    blocks: list[StrictStr] | None = None
    columns: list[StrictStr] | None = None


class BoundSummaryStatsParams(SummaryStatsParams):
    args: BoundSummaryStatsArgs = Field(default_factory=BoundSummaryStatsArgs)


BoundAggregateParams = Annotated[
    BoundSummaryStatsParams | DigestListParams, Field(discriminator="aggregation")
]


class TaskInput(_Model):
    """The one argument of run_task: the request and what the worker needs beside it."""

    request: Event = Field(
        description="A dev.wdf.task.requested.v1 event, as wdf_events.to_bytes writes it."
    )
    task_attempt: StrictInt = Field(ge=1)
    resume_counter: StrictInt = Field(ge=0)
    batch_id: str = Field(pattern=UUID_PATTERN)
    seed: StrictInt = Field(ge=0, le=SEED_LIMIT - 1)
    params: CompileParams | Input | BoundAggregateParams = Field(
        description="The params of the bound plan task, with no placeholder left."
    )
    cache_key: str | None = Field(
        pattern=SHA256_PATTERN, description="The compile cache key, null on any other task."
    )

    @field_serializer("request")
    def _canonical_request(self, request: Event) -> dict:
        # The same document as to_bytes, so the request keeps its bytes inside the payload.
        return request.model_dump(exclude_none=True)


class TaskResult(_Model):
    """The result of a successful run_task: references only, never bytes."""

    outputs: list[ArtifactReference] = Field(
        min_length=1,
        max_length=2,
        description="The declared outputs of one task type of staging.yaml, in order.",
    )
    log_tail: LogTailReference
    started_at: Time


class FailureDetails(_Model):
    """The details of the ApplicationError a worker raises, whose type is the failure class."""

    deterministic: StrictBool
    reason: StrictStr = Field(min_length=1, max_length=REASON_MAX)
    started_at: Time
    checkpoint: CheckpointReference | None = Field(
        description="The latest checkpoint the attempt uploaded."
    )
    log_tail: LogTailReference | None = Field(
        description="Null only when the upload of the tail itself failed."
    )


class RaisedFailure(_Model):
    """A failure as a worker raises it: the ApplicationError type and its details."""

    type: StrictStr = Field(min_length=1)
    details: FailureDetails


class HeartbeatDetails(_Model):
    """The details of each run_task heartbeat, so a lost worker still hands over its checkpoint."""

    progress: float | None = Field(ge=0, le=1)
    step: StrictInt | None = Field(ge=0)
    steps: StrictInt | None = Field(ge=1)
    checkpoint: CheckpointReference | None
    started_at: Time

    @model_validator(mode="after")
    def _progress_is_whole(self):
        triple = (self.progress, self.step, self.steps)
        if any(v is None for v in triple) and any(v is not None for v in triple):
            raise ValueError("progress, step and steps are all set or all null")
        if self.step is not None and self.step > self.steps:
            raise ValueError(f"step {self.step} is above steps {self.steps}")
        return self


Deterministic = StrictBool | Literal["from-error-file"]
Budget = Literal["guest", "infra", "free", "none"]
Action = Literal["invalidate-cache-and-rebuild", "previous-checkpoint", "re-place", "escalate-once"]


class FailureClass(_Model):
    class_: str = Field(alias="class", pattern="^[A-Za-z][A-Za-z0-9_]{0,63}$")
    source: Literal["workload", "artifact", "runner", "temporal", "placement"]
    pool: Pool | None = Field(
        default=None, description="Set exactly on a workload row, the pool of exit-classes.yaml."
    )
    deterministic: Deterministic
    budget: Budget
    action: Action | None = None

    @model_validator(mode="after")
    def _shape(self):
        if self.class_ == "ok":
            raise ValueError("ok is a success and has no row")
        if (self.pool is None) == (self.source == "workload"):
            raise ValueError(f"{self.class_}: a pool is set exactly on a workload row")
        return self


class FailureClasses(_Model):
    """failure-classes.yaml: every failure class with its budget and action."""

    classes: list[FailureClass] = Field(min_length=1)


class Retry(_Model):
    """A Temporal retry policy, where maximum_attempts 0 means no limit as in Temporal."""

    maximum_attempts: NonNegativeInt
    initial_interval_s: PositiveInt | None = None
    backoff_coefficient: float | None = Field(default=None, ge=1)
    maximum_interval_s: PositiveInt | None = None


class SingleAttempt(Retry):
    maximum_attempts: Literal[1]


class Bounded(Retry):
    maximum_attempts: PositiveInt


class Unbounded(Retry):
    maximum_attempts: Literal[0]


class PoolActivity(_Model):
    """An activity the workflow runs once, keeping the retry loop in the workflow."""

    name: str = Field(pattern=NAME)
    retry: SingleAttempt
    cancellation_type: Literal["WAIT_CANCELLATION_COMPLETED"]


class ControlActivity(_Model):
    """A regular activity whose retries end, so the workflow sees its typed error."""

    name: str = Field(pattern=NAME)
    retry: Bounded


class LocalActivity(_Model):
    """A local activity retried without limit, so an outage stalls a job and fails nothing."""

    name: str = Field(pattern=NAME)
    retry: Unbounded


class Activities(_Model):
    pool: list[PoolActivity] = Field(min_length=1)
    control: list[ControlActivity] = Field(min_length=1)
    local: list[LocalActivity] = Field(min_length=1)


class WorkflowNaming(_Model):
    type: str = Field(pattern="^[A-Z][A-Za-z0-9]{0,63}$")
    queue: str = Field(pattern=QUEUE)
    id: Literal["wdf_keys.workflow_id"]
    id_reuse_policy: Literal["REJECT_DUPLICATE"]
    versioning_behavior: Literal["PINNED"]
    deployment: str = Field(pattern=QUEUE)
    build_id_env: Literal["WDF_BUILD_ID"]


class ScheduleNaming(_Model):
    id: str = Field(pattern=QUEUE)
    queue: str = Field(pattern=QUEUE)
    workflow: str = Field(pattern="^[A-Z][A-Za-z0-9]{0,63}$")
    workflow_id: str = Field(pattern=QUEUE)
    activity: str = Field(pattern=NAME)


class Naming(_Model):
    """naming.yaml: every Temporal name the workflow, its workers and its callers share."""

    namespace: str = Field(pattern=QUEUE)
    workflow: WorkflowNaming
    control_queue: str = Field(
        pattern="^[a-z][a-z0-9-]{0,63}-<build id>$",
        description="The control queue of a build, with <build id> replaced by its build id.",
    )
    pools: dict[Pool, Annotated[str, Field(pattern=QUEUE)]]
    activities: Activities
    signals: list[Annotated[str, Field(pattern=NAME)]] = Field(min_length=1)
    queries: list[Annotated[str, Field(pattern=NAME)]] = Field(min_length=1)
    search_attributes: dict[
        Annotated[str, Field(pattern="^Wdf[A-Z][A-Za-z]{0,62}$")], Literal["keyword", "datetime"]
    ]
    schedules: dict[Literal["artifact_reaper"], ScheduleNaming]


class PoolTiming(_Model):
    cpu_milli: PositiveInt
    memory_mib: PositiveInt
    schedule_to_start_s: PositiveInt
    heartbeat_s: PositiveInt
    start_to_close_margin_s: PositiveInt


class Pools(_Model):
    """pools.yaml: pool table v0, the ci slots and the timeouts of each pool."""

    estimate: StrictBool
    schedule_to_start_ceiling_s: PositiveInt
    pools: dict[Pool, PoolTiming]

    @model_validator(mode="after")
    def _below_ceiling(self):
        for name, pool in self.pools.items():
            if pool.schedule_to_start_s > self.schedule_to_start_ceiling_s:
                raise ValueError(f"the first schedule-to-start of {name} is above the ceiling")
        return self


class ControlSignal(_Model):
    """The argument of the signals cancel, terminate and pause."""

    control_seq: StrictInt = Field(ge=1, le=CONTROL_SEQ_MAX)
    reason: StrictStr | None = Field(max_length=REASON_MAX)


class ResumeSignal(_Model):
    """The argument of the signal resume, with the row's pending patch or null."""

    control_seq: StrictInt = Field(ge=1, le=CONTROL_SEQ_MAX)
    manifest: ManifestReference | None


TaskState = Literal[
    "pending",
    "ready",
    "running",
    "succeeded",
    "skipped",
    "failed",
    "blocked",
    "paused",
    "cancelled",
]


class TaskStatus(_Model):
    id: str = Field(pattern=TASK_ID_PATTERN)
    state: TaskState
    task_attempt: StrictInt = Field(ge=0, description="0 for a task that never ran.")
    guest_left: StrictInt = Field(ge=0)
    infra_left: StrictInt = Field(ge=0)
    reschedules: StrictInt = Field(ge=0)
    last_class: str | None = Field(description="A class of failure-classes.yaml.")
    checkpoint: CheckpointReference | None = Field(description="The newest kept checkpoint.")


class StatusQuery(_Model):
    """The result of the query status."""

    job_status: str = Field(description="A status of contracts/status/status.yaml.")
    pause_reason: Literal["operator", "retries-exhausted", "resume-compile-failed"] | None
    resume_counter: StrictInt = Field(ge=0)
    control_seq: StrictInt = Field(ge=0, le=CONTROL_SEQ_MAX)
    tasks: list[TaskStatus] = Field(min_length=1, description="In ascending task id.")


# Each generated schema: its model, its contract and the size bound of its payload.
FILES = {
    "task-input": (TaskInput, "activity", TASK_INPUT_MAX_BYTES),
    "task-result": (TaskResult, "activity", TASK_RESULT_MAX_BYTES),
    "failure-details": (FailureDetails, "activity", FAILURE_DETAILS_MAX_BYTES),
    "heartbeat": (HeartbeatDetails, "activity", HEARTBEAT_MAX_BYTES),
    "failure-classes": (FailureClasses, "activity", None),
    "naming": (Naming, "temporal", None),
    "pools": (Pools, "temporal", None),
    "control-signal": (ControlSignal, "temporal", None),
    "resume-signal": (ResumeSignal, "temporal", None),
    "status-query": (StatusQuery, "temporal", STATUS_QUERY_MAX_BYTES),
}


def schema_path(name: str) -> str:
    return f"contracts/{FILES[name][1]}/{name}.schema.json"
