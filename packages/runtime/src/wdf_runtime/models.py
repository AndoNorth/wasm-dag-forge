"""The models of the workload contract files, the source of their generated schemas."""

import re
from importlib import resources
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    RootModel,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)
from wdf_jobdoc.models import MODULE_PATTERN, SEED_LIMIT, SHA256_PATTERN, TASK_ID_PATTERN, ArgName
from wdf_kernel.format import BLOCK_ID

# The contract files the runtime reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"

# The stderr line of a guest or a native library that reports progress, compact JSON, sorted keys.
HEARTBEAT = re.compile(
    r'HEARTBEAT (\{"progress":[0-9][0-9.e+-]*,"step":(?:0|[1-9][0-9]*),"steps":[1-9][0-9]*\})'
)
ERROR_FILE_MAX_BYTES = 4096
HEX_PATTERN = "^[0-9a-f]+$"
NAME = "^[a-z][a-z0-9_]{0,31}$"

Seed = Annotated[StrictInt, Field(ge=0, lt=SEED_LIMIT)]
TaskId = Annotated[str, Field(pattern=TASK_ID_PATTERN)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
Scalar = StrictBool | StrictInt | StrictStr


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Input(_Model):
    """input.json: the bound params of a simulate task, written canonical to /in/input.json."""

    args: dict[ArgName, Scalar | list[Scalar]] = Field(
        description="The bound arguments of the workload, with no placeholder left."
    )
    seed: Seed
    workload: StrictStr = Field(min_length=1, max_length=128)


class Error(_Model):
    """error.json: the optional failure report a guest writes to /out before it exits nonzero."""

    code: str = Field(pattern="^[a-z][a-z0-9-]{0,63}$")
    message: StrictStr
    deterministic: StrictBool = Field(description="Whether a retry would fail the same way.")


class Column(_Model):
    name: str = Field(pattern=NAME)
    type: str = Field(
        pattern="^[a-z]+$", description="One of the column types of wdf_kernel.format."
    )


class Kind(_Model):
    columns: list[Column] = Field(min_length=1)


class Results(_Model):
    """results.yaml: the block kinds of wdf.sim.v1 with their columns."""

    schema_: Literal["wdf.sim.v1"] = Field(alias="schema")
    kinds: dict[Annotated[str, Field(pattern=NAME)], Kind] = Field(min_length=1)


class Block(_Model):
    id: str = Field(pattern=f"^{BLOCK_ID.pattern}$")
    kind: str
    rows: list[dict[str, StrictInt | StrictFloat | StrictStr]] = Field(
        description="Each row's values, an int or a float as a number, a hex or a str as a string."
    )


class Expected(_Model):
    """The parsed values of a results.xml, the form of a golden vector's expected.json."""

    schema_: Literal["wdf.sim.v1"] = Field(alias="schema")
    seed: Seed
    blocks: list[Block]


class ExitClass(_Model):
    class_: str = Field(alias="class", pattern="^[a-z][a-z_]{0,63}$")
    trigger: str = Field(min_length=1)
    deterministic: StrictBool | Literal["from-error-file"]
    exit_code: int | None = Field(
        default=None, ge=0, le=125, description="The exit code of the pool's native library."
    )


class Pool(_Model):
    classes: list[ExitClass] = Field(min_length=1)


class ExitClasses(_Model):
    """exit-classes.yaml: how the exit of each pool's process is classified."""

    pools: dict[str, Pool] = Field(description="One entry per pool of contracts/job/registry.json.")


class Stats(_Model):
    count: PositiveInt
    mean: float
    std: float | None = Field(description="The sample standard deviation, null when count is 1.")
    min: StrictInt | StrictFloat
    max: StrictInt | StrictFloat


class SummaryStatsArgs(_Model):
    blocks: list[StrictStr] | None = None
    columns: list[StrictStr] | None = None


class DigestListArgs(_Model):
    pass


class Digest(_Model):
    task: TaskId
    seed: Seed
    digest: str = Field(pattern=HEX_PATTERN)


class _Summary(_Model):
    schema_: Literal["wdf.summary/1"] = Field(alias="schema")
    inputs: NonNegativeInt = Field(description="The number of entries of the index.")
    missing: list[TaskId] = Field(description="The inputs that did not succeed, in task id order.")
    rows: NonNegativeInt = Field(description="The number of rows read.")


class SummaryStatsSummary(_Summary):
    aggregation: Literal["summary_stats"]
    args: SummaryStatsArgs
    blocks: dict[str, dict[str, Stats]] = Field(
        description="Per block id and numeric column, the statistics over every input."
    )


class DigestListSummary(_Summary):
    aggregation: Literal["digest_list"]
    args: DigestListArgs
    digests: list[Digest] = Field(description="One entry per succeeded input, in index order.")


class Summary(RootModel[SummaryStatsSummary | DigestListSummary]):
    """summary.json: the canonical output of an aggregate task."""

    root: SummaryStatsSummary | DigestListSummary = Field(discriminator="aggregation")


class KeyInputs(_Model):
    source_hash: Sha256
    lockfile_hash: Sha256
    componentize_py_version: str = Field(min_length=1)
    wit_version: str = Field(min_length=1)
    entry: str = Field(pattern=MODULE_PATTERN)


class Tools(_Model):
    componentize_py: str = Field(min_length=1)
    wasm_tools: str = Field(min_length=1)
    uv: str = Field(min_length=1)
    guest_python: str = Field(min_length=1)


class Component(_Model):
    sha256: Sha256
    size: PositiveInt


class Timings(_Model):
    unpack: NonNegativeFloat
    install: NonNegativeFloat
    componentize: NonNegativeFloat
    check: NonNegativeFloat


class BuildInfo(_Model):
    """build-info.json: what a compile task built, from what and with which tools."""

    schema_: Literal["wdf.build-info/1"] = Field(alias="schema")
    cache_key: Sha256 = Field(description="wdf_keys.compile_cache_key over the inputs.")
    inputs: KeyInputs
    tools: Tools
    component: Component
    imports: list[str] = Field(
        description="The imports of the component as wasm-tools reports them."
    )
    exports: list[str] = Field(
        description="The exports of the component as wasm-tools reports them."
    )
    timings_s: Timings


# Each contract file with its model and the schema generated from it.
FILES = {
    "input": (Input, "contracts/workload/input.schema.json"),
    "error": (Error, "contracts/workload/error.schema.json"),
    "results": (Results, "contracts/workload/results.schema.json"),
    "exit-classes": (ExitClasses, "contracts/workload/exit-classes.schema.json"),
    "summary": (Summary, "contracts/workload/summary.schema.json"),
    "build-info": (BuildInfo, "contracts/workload/build-info.schema.json"),
}
