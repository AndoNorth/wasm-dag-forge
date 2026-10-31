"""The platform event and the data models that are the source of the generated schemas."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class Event(BaseModel):
    """A CloudEvent as the platform profile defines it, with the data as a plain mapping.

    The data is validated against the JSON Schema its `dataschema` names, so a reader that knows
    only an older minor can still carry the added optional fields.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    specversion: Literal["1.0"]
    id: str
    source: str
    type: str
    time: str
    datacontenttype: Literal["application/json"]
    dataschema: str
    subject: str
    tenant: str
    jobid: str
    taskid: str | None = None
    attempt: int | None = None
    priorityclass: str
    jobclass: str
    traceparent: str
    tracestate: str | None = None
    data: dict[str, Any]


class ManifestReference(BaseModel):
    """A manifest reference, a subset of contracts/artifact/reference.schema.json."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(pattern="^s3://wdf-sources/.+$")
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=255, pattern="^[^/]+$")
    kind: Literal["manifest"]


class AcceptedData(BaseModel):
    """Data of dev.wdf.job.accepted.v1: a pointer to the job and never the job."""

    model_config = ConfigDict(extra="forbid")

    manifest: ManifestReference
    batch_id: str = Field(pattern=UUID_PATTERN)
    submission_key: str = Field(min_length=1, max_length=256)


class NoData(BaseModel):
    """Data of an event whose type and context attributes say everything."""

    model_config = ConfigDict(extra="forbid")


class Reason(BaseModel):
    """Data of an event that records why something happened."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1024)


class Request(BaseModel):
    """Data of a control request, where the reason is the operator's own words."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1024)


class AdmittedData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(
        pattern="^job-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )


class PartialData(BaseModel):
    """The tasks that did not run to completion, so a partial run is never read as complete."""

    model_config = ConfigDict(extra="forbid")

    skipped_tasks: list[str] = Field(
        min_length=1, max_length=1000, json_schema_extra={"uniqueItems": True}
    )


class DeadLetteredData(BaseModel):
    """A message that parsed far enough to name a job and was then refused."""

    model_config = ConfigDict(extra="forbid")

    dlq_reason: Literal["MALFORMED", "UNSUPPORTED_VERSION"]
    detail: str = Field(max_length=1024)


class PlacedData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue: str = Field(min_length=1, max_length=255)


class ProducerRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: str = Field(pattern=UUID_PATTERN)
    task: str = Field(pattern="^[a-z0-9][a-z0-9._-]{0,62}$")
    attempt: str = Field(pattern="^r[0-9]+a[0-9]+$")


class CheckpointReference(BaseModel):
    """A checkpoint reference, a subset of contracts/artifact/reference.schema.json."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(pattern="^s3://wdf-checkpoints/.+$")
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=255, pattern="^[^/]+$")
    kind: Literal["checkpoint"]
    producer: ProducerRef


class CheckpointCommittedData(BaseModel):
    """The `sequence` is the position among the attempt's checkpoints and feeds the event id."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    checkpoint: CheckpointReference


class RetriesExhaustedData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1024)


class SkippedData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["compile-cache-hit"]
    cache_key: str = Field(pattern="^[0-9a-f]{64}$")


BUCKETS = "(sources|components|results|checkpoints)"


class _Reference(BaseModel):
    """The fields every artifact reference has, as contracts/artifact/reference.schema.json."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(pattern=f"^s3://wdf-{BUCKETS}/.+$")
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=255, pattern="^[^/]+$")


class ContentReference(_Reference):
    """A content-addressed object, which has no producer."""

    kind: Literal["manifest", "source", "index", "component", "build-info"]


class AttemptReference(_Reference):
    """An attempt-scoped object, which names the attempt that wrote it."""

    kind: Literal["result", "output", "log-tail", "summary", "checkpoint"]
    producer: ProducerRef


class ResultReference(AttemptReference):
    kind: Literal["result", "summary"]


ArtifactReference = Annotated[ContentReference | AttemptReference, Field(discriminator="kind")]

TIME_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"


class TaskRequestData(BaseModel):
    """Data of dev.wdf.task.requested.v1, the input of a task activity.

    `inputs` is a list of references and is never the inputs themselves, so an aggregate over many
    results passes one index reference and the size budget guards the rest.
    """

    model_config = ConfigDict(extra="forbid")

    task_type: Literal["compile", "simulate", "aggregate"]
    cpu_cores: float = Field(gt=0)
    memory_mib: int = Field(ge=1)
    inputs: list[ArtifactReference]
    resume_checkpoint: CheckpointReference | None = None
    deadline: str = Field(pattern=TIME_PATTERN)


class ResultReadyData(BaseModel):
    """Data of dev.wdf.task.result.ready.v1: one reference to the result, never its bytes."""

    model_config = ConfigDict(extra="forbid")

    result: ResultReference


class JobParams(BaseModel):
    """The per-job block that admission reads from the row and passes beside the accepted event.

    It never travels in an event, so it has no schema file and no type.
    """

    model_config = ConfigDict(extra="forbid")

    seed: int = Field(ge=0)
    parameters: dict[str, Any]
    class_override: str | None = None


# One data model per (type without the dev.wdf prefix and major, major.minor).
DATA_MODELS: dict[tuple[str, str], type[BaseModel]] = {
    ("job.accepted", "1.0"): AcceptedData,
    ("job.published", "1.0"): NoData,
    ("job.admitted", "1.0"): AdmittedData,
    ("job.started", "1.0"): NoData,
    ("job.paused", "1.0"): NoData,
    ("job.pause.requested", "1.0"): Request,
    ("job.resume.requested", "1.0"): Request,
    ("job.resumed", "1.0"): NoData,
    ("job.cancel.requested", "1.0"): Request,
    ("job.terminate.requested", "1.0"): Request,
    ("job.succeeded", "1.0"): NoData,
    ("job.partial", "1.0"): PartialData,
    ("job.failed", "1.0"): Reason,
    ("job.cancelled", "1.0"): NoData,
    ("job.terminated", "1.0"): NoData,
    ("job.dead.lettered", "1.0"): DeadLetteredData,
    ("task.placed", "1.0"): PlacedData,
    ("task.started", "1.0"): NoData,
    ("task.checkpoint.committed", "1.0"): CheckpointCommittedData,
    ("task.attempt.failed", "1.0"): Reason,
    ("task.retries.exhausted", "1.0"): RetriesExhaustedData,
    ("task.succeeded", "1.0"): NoData,
    ("task.failed", "1.0"): Reason,
    ("task.skipped", "1.0"): SkippedData,
    ("task.requested", "1.0"): TaskRequestData,
    ("task.result.ready", "1.0"): ResultReadyData,
}

# The date each major of a type was introduced, which the compatibility window counts from.
INTRODUCED: dict[tuple[str, int], str] = {(name, 1): "2026-10-31" for name, _ in DATA_MODELS}

# Task types whose event belongs to one attempt and so carries `attempt` and an attempt identity.
ATTEMPT_TYPES = frozenset(
    {
        "task.started",
        "task.checkpoint.committed",
        "task.attempt.failed",
        "task.succeeded",
        "task.failed",
        "task.result.ready",
    }
)
