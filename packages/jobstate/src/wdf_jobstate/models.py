"""The row model of accepted_job, the source of contracts/state/schemas/job-row/1.json."""

from datetime import datetime
from importlib import resources
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

CONTRACT = resources.files(__package__) / "contract"
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# The vocabularies are read from the contracts and never retyped here.
STATUSES = tuple(yaml.safe_load((CONTRACT / "status.yaml").read_text())["statuses"])
CLASSES = tuple(yaml.safe_load((CONTRACT / "limits.yaml").read_text())["profiles"]["ci"]["classes"])
ACTIONS = ("pause", "resume", "cancel", "terminate")

Status = Literal[STATUSES]
JobClass = Literal[CLASSES]
Action = Literal[ACTIONS]


class JobRow(BaseModel):
    """One row per job, with the columns of contracts/state/README.md in the same order."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=UUID_PATTERN)
    tenant: str = Field(min_length=1)
    submission_key: str = Field(min_length=1, max_length=256)
    batch_id: str = Field(pattern=UUID_PATTERN)
    priority_class: str = Field(min_length=1)
    job_class: JobClass = "short"
    status: Status = "accepted"
    manifest_ref: dict[str, Any]
    manifest_sha256: str | None = Field(
        default=None,
        pattern="^[0-9a-f]{64}$",
        description="Generated from manifest_ref and never written.",
    )
    schema_version: str = Field(min_length=1)
    traceparent: str = Field(min_length=1)
    tracestate: str | None = None
    published_at: datetime | None = None
    seed: int = Field(ge=0, lt=2**53)
    parameters: dict[str, Any]
    fingerprint: str = Field(pattern="^[0-9a-f]{64}$")
    requested_action: Action | None = None
    control_seq: int = Field(default=0, ge=0)
    control_delivered_at: datetime | None = None
    pending_patch_ref: dict[str, Any] | None = None
    created_at: datetime | None = Field(default=None, description="Set by the database.")
    updated_at: datetime | None = Field(default=None, description="Set by the database.")
    terminal_at: datetime | None = Field(
        default=None, description="Set by the guarded move into a terminal status."
    )
