"""The row models of artifact and artifact_use, the source of contracts/state/schemas."""

from datetime import datetime
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from wdf_events import layout

# The contract files the runtime reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
HASH_PATTERN = "^[0-9a-f]{64}$"

# The kinds are read from the layout and never retyped, and a compile cache entry is the one more.
LAYOUT = layout()
KINDS = (*LAYOUT["kinds"], "compile-cache")

Kind = Literal[KINDS]


class ArtifactRow(BaseModel):
    """A reference or a compile cache entry, with the columns of contracts/state/README.md.

    The producer fields have no default, because the attempt is stored as the reference carries it
    and never computed: all three are null for the content-addressed kinds and for a cache entry.
    """

    model_config = ConfigDict(extra="forbid")

    row_id: int | None = Field(default=None, description="Generated identity, never written.")
    kind: Kind
    uri: str | None = None
    bucket: str | None = None
    key: str | None = None
    sha256: str | None = Field(default=None, pattern=HASH_PATTERN)
    size: int | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=255, pattern="^[^/]+$")
    producer_job: str | None = Field(pattern=UUID_PATTERN)
    producer_task: str | None = Field(pattern="^[a-z0-9][a-z0-9._-]{0,62}$")
    producer_attempt: str | None = Field(pattern="^r[0-9]+a[0-9]+$")
    created_at: datetime | None = Field(default=None, description="Set by the database.")
    deleted_at: datetime | None = None
    cache_key: str | None = Field(default=None, pattern=HASH_PATTERN)
    wasm_uri: str | None = None
    componentize_py_version: str | None = None
    wit_version: str | None = None
    last_used_at: datetime | None = None
    build_info_uri: str | None = Field(
        default=None, description="The build-info a cache entry names, null for a reference."
    )
    purged_at: datetime | None = Field(
        default=None, description="Set when the object was removed from the store."
    )


class ArtifactUse(BaseModel):
    """The link of a job to a content-addressed blob that it holds, as in the state README."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(pattern=UUID_PATTERN)
    artifact_row_id: int
    created_at: datetime | None = Field(default=None, description="Set by the database.")
    last_linked_at: datetime | None = Field(
        default=None, description="Moved forward each time the job links the blob again."
    )
