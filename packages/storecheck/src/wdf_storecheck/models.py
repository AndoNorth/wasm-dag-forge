"""The models of the store contract files, the source of contracts/store/store.schema.json."""

from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

# The contract files the runtime reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"

# The identities of the store, one per kind of caller. The set is this contract's own.
ROLES = (
    "admin",
    "submit",
    "indexer",
    "compile",
    "simulate",
    "aggregate",
    "workflow",
    "export",
    "reaper",
)
ACTIONS = ("get", "put", "list", "multipart", "delete", "bucket", "lifecycle")
RULE_KINDS = ("abort-incomplete-multipart", "expire")
GAPS = ("lifecycle-edit-by-writer", "delete-while-full")

Action = Literal[ACTIONS]
RuleKind = Literal[RULE_KINDS]
Gap = Literal[GAPS]


class Roles(BaseModel):
    """roles.yaml: for each role the actions it may take on each bucket it names."""

    model_config = ConfigDict(extra="forbid")

    roles: dict[str, dict[str, list[Action]]]


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RuleKind
    window: str = Field(
        pattern="^(windows|housekeeping)\\.[a-z0-9-]+\\.[a-z_]+$",
        description="A key of contracts/state/retention.yaml.",
    )


class BucketRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[Rule]


class Lifecycle(BaseModel):
    """lifecycle.yaml: the rules of each bucket and how often the lifecycle job looks."""

    model_config = ConfigDict(extra="forbid")

    detection_interval_minutes: PositiveInt
    buckets: dict[str, BucketRules]


class KnownGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap: Gap
    roles: list[str]


class Product(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(min_length=1, description="A key of the images map of deploy/versions.yaml.")
    known_gaps: list[KnownGap]


class Products(BaseModel):
    """products.yaml: the tested products and the behaviour each is known not to have."""

    model_config = ConfigDict(extra="forbid")

    products: dict[str, Product]


FILES = (Roles, Lifecycle, Products)
