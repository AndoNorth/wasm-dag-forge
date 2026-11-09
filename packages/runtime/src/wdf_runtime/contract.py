"""Loads one workload contract file and checks it against the models, the format and the registry.

Usage: python -m wdf_runtime.contract --check <file>
A .yaml file is exit-classes.yaml or results.yaml, a .json file is an input, error, summary, build
info or expected values file, a .xml file is a results document and a .stderr file is guest stderr.
Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
"""

import json
import math
import re
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError
from wdf_kernel.format import COLUMN_TYPES, FormatError, check_document, format_value, parse_value
from wdf_keys import compile_cache_key

from .models import (
    CONTRACT,
    ERROR_FILE_MAX_BYTES,
    HEARTBEAT,
    BuildInfo,
    Error,
    ExitClasses,
    Expected,
    Input,
    Results,
    Summary,
    SummaryStatsSummary,
)

# Exit codes that every native library keeps for itself: success and usage.
RESERVED_EXIT_CODES = {0, 2}
YAML_TOP_LEVEL = {"pools": ExitClasses, "kinds": Results}
JSON_SCHEMAS = {"wdf.build-info/1": BuildInfo, "wdf.summary/1": Summary, "wdf.sim.v1": Expected}
CANONICAL = (Input, Summary, Expected)


class Refused(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")


def wit_version() -> str:
    """The version in the package line of task.wit, the version of the guest contract."""
    text = (CONTRACT / "wit" / "task.wit").read_text()
    return re.search(r"^package wdf:task@([0-9A-Za-z.+-]+);$", text, re.MULTILINE).group(1)


def kinds() -> dict[str, dict[str, str]]:
    """Each block kind of results.yaml with its columns and their types."""
    document = yaml.safe_load((CONTRACT / "results.yaml").read_text())
    return {
        name: {c["name"]: c["type"] for c in kind["columns"]}
        for name, kind in document["kinds"].items()
    }


def registry_pools() -> set[str]:
    types = json.loads((CONTRACT / "registry.json").read_text())["task_types"]
    return {entry["pool"] for entry in types.values()}


def canonical(value) -> str:
    """The one serialization of a canonical JSON file, so equal values give equal bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def heartbeat(line: str) -> dict | None:
    """The progress of a heartbeat line, None for another line, Refused for a broken heartbeat."""
    if not line.startswith("HEARTBEAT"):
        return None
    match = HEARTBEAT.fullmatch(line)
    if match is None:
        raise Refused("bad-heartbeat", f"{line[:80]!r} does not match the heartbeat pattern")
    try:
        beat = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise Refused("bad-heartbeat", f"{line[:80]!r} is not JSON: {error}") from error
    progress = beat["progress"]
    if not (math.isfinite(progress) and 0 <= progress <= 1):
        raise Refused("bad-heartbeat", f"progress {progress} is outside 0 to 1")
    if beat["step"] > beat["steps"]:
        raise Refused("bad-heartbeat", f"step {beat['step']} is above steps {beat['steps']}")
    return beat


def check_exit_classes(model: ExitClasses) -> None:
    seen: set[str] = set()
    for pool, entry in model.pools.items():
        codes: set[int] = set()
        for exit_class in entry.classes:
            if exit_class.class_ in seen:
                raise Refused("duplicate-class", f"{exit_class.class_} is listed twice")
            seen.add(exit_class.class_)
            code = exit_class.exit_code
            if code is None:
                continue
            if code in RESERVED_EXIT_CODES or code in codes:
                raise Refused("exit-code-taken", f"{exit_class.class_} of {pool} takes exit {code}")
            codes.add(code)
    pools = registry_pools()
    for pool in model.pools:
        if pool not in pools:
            raise Refused("unknown-pool", f"{pool} is not a pool of the registry")
    for pool in sorted(pools - set(model.pools)):
        raise Refused("schema-violation", f"the pool {pool} has no classes")


def check_results(model: Results) -> None:
    for name, kind in model.kinds.items():
        names = [column.name for column in kind.columns]
        if len(set(names)) != len(names):
            raise Refused("schema-violation", f"the kind {name} lists a column twice")
        for column in kind.columns:
            if column.type not in COLUMN_TYPES:
                raise Refused(
                    "unknown-column-type", f"{name}.{column.name} has the type {column.type}"
                )


def check_value(column_type: str, value) -> None:
    """A JSON value of a column: a number for int and float, the attribute text for hex and str."""
    if column_type in ("hex", "str"):
        if not isinstance(value, str):
            raise FormatError("bad-number-format", f"{value!r} is not a {column_type} value")
        parse_value(column_type, value)
    else:
        format_value(column_type, value)


def check_expected(model: Expected) -> None:
    known = kinds()
    ids: set[str] = set()
    for block in model.blocks:
        if block.id in ids:
            raise Refused("duplicate-block-id", f"the block id {block.id} appears twice")
        ids.add(block.id)
        if block.kind not in known:
            raise Refused("unknown-kind", f"block {block.id} has the kind {block.kind}")
        columns = known[block.kind]
        for row in block.rows:
            if set(row) != set(columns):
                raise Refused(
                    "schema-violation", f"a row of {block.id} has the columns {sorted(row)}"
                )
            for column, value in row.items():
                check_value(columns[column], value)


def check_summary(model: Summary) -> None:
    summary = model.root
    if summary.missing != sorted(set(summary.missing)):
        raise Refused("schema-violation", "missing is not in ascending task id order")
    if isinstance(summary, SummaryStatsSummary):
        for block, columns in summary.blocks.items():
            for column, stats in columns.items():
                if (stats.std is None) != (stats.count == 1):
                    raise Refused(
                        "schema-violation",
                        f"{block}.{column} has std {stats.std} for {stats.count}",
                    )
        return
    tasks = [digest.task for digest in summary.digests]
    if tasks != sorted(set(tasks)):
        raise Refused("schema-violation", "digests are not in ascending task id order")


def check_build_info(model: BuildInfo) -> None:
    inputs = model.inputs
    if inputs.wit_version != wit_version():
        raise Refused(
            "wit-version-mismatch",
            f"the build names WIT {inputs.wit_version}, task.wit is {wit_version()}",
        )
    key = compile_cache_key(
        inputs.source_hash,
        inputs.lockfile_hash,
        inputs.componentize_py_version,
        inputs.wit_version,
        inputs.entry,
    )
    if model.cache_key != key:
        raise Refused("schema-violation", f"cache_key is not the key of the inputs, {key}")


CHECKS = {
    ExitClasses: check_exit_classes,
    Results: check_results,
    Expected: check_expected,
    Summary: check_summary,
    BuildInfo: check_build_info,
}


def model_of(document, top_level: dict) -> type[BaseModel]:
    if isinstance(document, dict):
        for key, model in top_level.items():
            if key in document:
                return model
    raise Refused("schema-violation", "the file is none of the files of the workload contract")


def json_model(document) -> type[BaseModel]:
    if isinstance(document, dict) and document.get("schema") in JSON_SCHEMAS:
        return JSON_SCHEMAS[document["schema"]]
    return model_of(document, {"workload": Input, "code": Error})


def check_model(model: type[BaseModel], document) -> None:
    try:
        parsed = model.model_validate(document)
    except ValidationError as error:
        raise Refused("schema-violation", " ".join(str(error).split())) from error
    if model in CHECKS:
        CHECKS[model](parsed)


def check(path: Path) -> None:
    """Raises Refused with the first rule that the file breaks."""
    if path.suffix == ".xml":
        with path.open("rb") as source:
            check_document(source, kinds())
    elif path.suffix == ".stderr":
        for line in path.read_text().splitlines():
            heartbeat(line)
    elif path.suffix == ".json":
        text = path.read_text()
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise Refused("schema-violation", f"the file is not JSON: {error}") from error
        model = json_model(document)
        if model is Error and len(text.encode()) > ERROR_FILE_MAX_BYTES:
            raise Refused("schema-violation", f"error.json is above {ERROR_FILE_MAX_BYTES} bytes")
        if model in CANONICAL and canonical(document) != text:
            raise Refused("schema-violation", "the file is not canonical JSON")
        check_model(model, document)
    else:
        document = yaml.safe_load(path.read_text())
        check_model(model_of(document, YAML_TOP_LEVEL), document)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--check" or not Path(argv[1]).is_file():
        print("usage: contract --check <.yaml|.json|.xml|.stderr file>", file=sys.stderr)
        return 2
    try:
        check(Path(argv[1]))
    except yaml.YAMLError as error:
        print(f"reason: schema-violation: {' '.join(str(error).split())}", file=sys.stderr)
        return 1
    except (Refused, FormatError) as refused:
        print(f"reason: {refused}", file=sys.stderr)
        return 1
    print(f"ok {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
