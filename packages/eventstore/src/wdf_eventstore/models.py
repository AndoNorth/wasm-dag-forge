"""The row model of event and the retention file, the sources of the generated schemas."""

from datetime import datetime
from importlib import resources

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

# The contract files the runtime reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class EventRow(BaseModel):
    """One row per event. The fields are the columns of contracts/state/README.md in the same order.

    The attempt and the task have no default, so a writer states what the event carries.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(pattern="^[^#]+#[^#]+$")
    event_time: datetime
    type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    tenant: str = Field(min_length=1)
    job_id: str = Field(pattern=UUID_PATTERN)
    task_id: str | None
    attempt: int | None = Field(ge=0)
    body: str = Field(min_length=1, description="The canonical bytes of the event, as text.")


class WindowRule(BaseModel):
    """An age rule that removes data, so it is held to the floor."""

    model_config = ConfigDict(extra="forbid")

    drop_after_days: PositiveInt | None = None
    expire_after_days: PositiveInt | None = None


class HousekeepingRule(BaseModel):
    """An age rule that removes nothing a job can still need."""

    model_config = ConfigDict(extra="forbid")

    compress_after_days: PositiveInt | None = None
    abort_incomplete_multipart_after_days: PositiveInt | None = None


class Retention(BaseModel):
    """contracts/state/retention.yaml, keyed by the table or the bucket that a rule applies to."""

    model_config = ConfigDict(extra="forbid")

    windows: dict[str, WindowRule]
    housekeeping: dict[str, HousekeepingRule]
