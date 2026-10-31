"""The job document: the models are the source of the exported JSON Schema.

Values that can hold a placeholder, and the decimal `cpu`, are checked by hand so that every fault
carries a stable code from contracts/job/errors.yaml instead of a library error type.
"""

import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainValidator,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .registry import PARAMETER_TYPES

SCHEMA_ID = "urn:wdf:schema:job:1.0"

TASK_ID_PATTERN = "^[a-z0-9][a-z0-9._-]{0,62}$"
NAME_PATTERN = "^[a-z][a-z0-9_]{0,31}$"
MODULE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
OUTPUT_PATTERN = "^[a-z][a-z0-9-]{0,31}$"
SHA256_PATTERN = "^[0-9a-f]{64}$"

# Seeds stay below 2**53, so no JSON reader rounds one.
SEED_LIMIT = 2**53
# Cores are bounded so that the integer millicore value of a plan stays small whatever the exponent.
MAX_CPU_CORES = 1_000_000

TaskId = Annotated[str, StringConstraints(pattern=TASK_ID_PATTERN)]

PLACEHOLDER_SCHEMA = {
    "title": "Placeholder",
    "type": "object",
    "additionalProperties": False,
    "required": ["param"],
    "properties": {"param": {"type": "string", "pattern": NAME_PATTERN}},
}
_SCALAR_SCHEMAS = [{"type": "boolean"}, {"type": "integer"}, {"type": "string"}]


class Placeholder(BaseModel):
    """The closed one-key object that stands for a per-job value, `{"param": "<name>"}`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    param: str = Field(pattern=NAME_PATTERN)


def _placeholder(value: dict) -> Placeholder:
    name = value.get("param")
    if len(value) != 1 or not isinstance(name, str) or not re.fullmatch(NAME_PATTERN, name):
        raise PydanticCustomError(
            "placeholder-malformed", "a placeholder is one key, param, naming a parameter"
        )
    return Placeholder(param=name)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, bool | int | str)


def _arg(value: Any) -> Any:
    if isinstance(value, dict):
        return _placeholder(value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict) and "param" in item:
                raise PydanticCustomError(
                    "placeholder-misplaced",
                    "a placeholder is not a list element ({index})",
                    {"index": index},
                )
            if not _is_scalar(item):
                raise PydanticCustomError(
                    "bad-type",
                    "list element {index} is not an integer, string or boolean",
                    {"index": index},
                )
        return value
    if not _is_scalar(value):
        raise PydanticCustomError(
            "bad-type", "an argument is an integer, string, boolean or list of them"
        )
    return value


ArgValue = Annotated[
    Any,
    PlainValidator(_arg),
    WithJsonSchema(
        {
            "anyOf": [
                *_SCALAR_SCHEMAS,
                {"type": "array", "items": {"anyOf": _SCALAR_SCHEMAS}},
                PLACEHOLDER_SCHEMA,
            ]
        }
    ),
]
ArgName = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]


def _string_list(value: Any) -> Any:
    if isinstance(value, dict):
        return _placeholder(value)
    if not isinstance(value, list):
        raise PydanticCustomError("bad-type", "a list of strings or a placeholder is expected")
    for index, item in enumerate(value):
        if isinstance(item, dict) and "param" in item:
            raise PydanticCustomError(
                "placeholder-misplaced",
                "a placeholder is not a list element ({index})",
                {"index": index},
            )
        if not isinstance(item, str):
            raise PydanticCustomError(
                "bad-type", "list element {index} is not a string", {"index": index}
            )
    return value


StringList = Annotated[
    Any,
    PlainValidator(_string_list),
    WithJsonSchema({"anyOf": [{"type": "array", "items": {"type": "string"}}, PLACEHOLDER_SCHEMA]}),
]


def _seed(value: Any) -> Any:
    if isinstance(value, dict):
        return _placeholder(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PydanticCustomError("bad-type", "a seed is an integer or a placeholder")
    if not 0 <= value < SEED_LIMIT:
        raise PydanticCustomError("bad-value", "a seed is an integer from 0 below 2**53")
    return value


Seed = Annotated[
    Any,
    PlainValidator(_seed),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer", "minimum": 0, "maximum": SEED_LIMIT - 1},
                PLACEHOLDER_SCHEMA,
            ]
        }
    ),
]


def _cpu(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise PydanticCustomError("bad-type", "cpu is a number of cores")
    cores = Decimal(value)
    if not cores.is_finite() or not 0 < cores <= MAX_CPU_CORES:
        raise PydanticCustomError(
            "bad-value", "cpu is above 0 and at most {max} cores", {"max": MAX_CPU_CORES}
        )
    if cores.normalize().as_tuple().exponent < -3:
        raise PydanticCustomError("bad-value", "cpu has at most three decimal places")
    return cores


Cpu = Annotated[
    Any,
    PlainValidator(_cpu),
    WithJsonSchema(
        {
            "type": "number",
            "exclusiveMinimum": 0,
            "maximum": MAX_CPU_CORES,
            "description": "cores, with at most three decimal places",
        }
    ),
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceReference(_Model):
    """The content-addressed reference of a source bundle, the `source` kind of the artifacts."""

    uri: str = Field(pattern="^s3://wdf-sources/sha256/[0-9a-f]{2}/[0-9a-f]{64}$")
    sha256: str = Field(pattern=SHA256_PATTERN)
    size: StrictInt = Field(ge=0)
    name: str = Field(min_length=1, max_length=255, pattern="^[^/]+$")
    kind: Literal["source"]

    @model_validator(mode="after")
    def _uri_is_the_content_address(self):
        if self.uri != f"s3://wdf-sources/sha256/{self.sha256[:2]}/{self.sha256}":
            raise PydanticCustomError(
                "bad-value", "the uri is not the content address of the sha256"
            )
        return self


class IndexReference(SourceReference):
    """The content-addressed reference of an aggregate index object, the `index` kind."""

    kind: Literal["index"]  # type: ignore[assignment]


class InputRef(_Model):
    """A logical reference to a declared output of another task of the same job."""

    task: TaskId
    output: str = Field(pattern=OUTPUT_PATTERN)


class CompileParams(_Model):
    source: SourceReference
    entry: str = Field(pattern=MODULE_PATTERN, max_length=128)


class SimulateParams(_Model):
    workload: StrictStr = Field(min_length=1, max_length=128)
    args: dict[ArgName, ArgValue] = Field(default_factory=dict)
    seed: Seed


class _AggregateParams(_Model):
    index: IndexReference | None = None


class SummaryStatsArgs(_Model):
    blocks: StringList | None = None
    columns: StringList | None = None


class DigestListArgs(_Model):
    pass


class SummaryStatsParams(_AggregateParams):
    aggregation: Literal["summary_stats"]
    args: SummaryStatsArgs = Field(default_factory=SummaryStatsArgs)


class DigestListParams(_AggregateParams):
    aggregation: Literal["digest_list"]
    args: DigestListArgs = Field(default_factory=DigestListArgs)


AggregateParams = Annotated[
    SummaryStatsParams | DigestListParams, Field(discriminator="aggregation")
]


class _Task(_Model):
    depends_on: list[TaskId] = Field(default_factory=list)
    inputs: list[InputRef] = Field(default_factory=list)


class _Resourced(_Task):
    cpu: Cpu
    memory_mib: StrictInt = Field(ge=1)
    run_timeout_s: StrictInt = Field(ge=1)

    @property
    def cpu_milli(self) -> int:
        return int(self.cpu * 1000)


class CompileTask(_Task):
    """A compile task has no resources, timeout or retries: the compile slot budget applies."""

    type: Literal["compile"]
    params: CompileParams


class SimulateTask(_Resourced):
    type: Literal["simulate"]
    retries: StrictInt = Field(ge=0)
    checkpoint: StrictBool = False
    params: SimulateParams


class AggregateTask(_Resourced):
    type: Literal["aggregate"]
    retries: StrictInt = Field(default=0, ge=0)
    allow_partial: StrictBool = False
    params: AggregateParams


Task = Annotated[CompileTask | SimulateTask | AggregateTask, Field(discriminator="type")]


class Document(_Model):
    """A job template: tasks keyed by id, and the declarations of the per-job parameters."""

    schema_: Literal["urn:wdf:schema:job:1.0"] = Field(alias="schema")
    parameters: dict[ArgName, Literal[*PARAMETER_TYPES]] = Field(default_factory=dict)
    max_parallel: StrictInt | None = Field(default=None, ge=1)
    tasks: dict[TaskId, Task] = Field(min_length=1)

    @field_validator("parameters", mode="after")
    @classmethod
    def _seed_is_reserved(cls, value):
        if value.get("seed", "seed") != "seed":
            raise PydanticCustomError(
                "bad-parameter-declaration", "the reserved parameter seed has the type seed"
            )
        return value
