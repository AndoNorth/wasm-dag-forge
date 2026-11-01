"""The row model of artifact, the source of contracts/state/schemas/artifact-row/1.json."""

from datetime import datetime
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

# The contract files the runtime reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
HASH_PATTERN = "^[0-9a-f]{64}$"

# The kinds are read from the layout and never retyped, and a compile cache entry is the one more.
KINDS = (*yaml.safe_load((CONTRACT / "layout.yaml").read_text())["kinds"], "compile-cache")

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
