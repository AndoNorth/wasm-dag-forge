"""The platform event and the data models that are the source of the generated schemas."""

from typing import Any, Literal

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


# One data model per (type without the dev.wdf prefix and major, major.minor).
DATA_MODELS: dict[tuple[str, str], type[BaseModel]] = {
    ("job.accepted", "1.0"): AcceptedData,
}
