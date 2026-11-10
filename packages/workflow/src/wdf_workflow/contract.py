"""Loads one file of the activity or Temporal contract and checks it against the models and the
contract files of the other contracts, read through package data.

Usage: python -m wdf_workflow.contract --check FILE [--as NAME]
NAME is a key of models.FILES. Without it, the name is the directory under vectors/ that holds
the file, or else the file's stem, so naming.yaml is checked as naming.
A failure-details file is the pair {type, details} that a worker raises.
Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
"""

import argparse
import json
import sys
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError
from wdf_events import EventError, check_task_request, to_bytes
from wdf_jobdoc.models import CompileParams
from wdf_runtime.models import Input

from .models import (
    CONTRACT,
    FILES,
    FailureClass,
    FailureClasses,
    Naming,
    Pools,
    RaisedFailure,
    StatusQuery,
    TaskInput,
    TaskResult,
)

REPORTED_SOURCES = {"workload", "artifact", "runner"}
CONTRACT_SOURCES = {"runner", "temporal", "placement"}
PARAMS = {"compile": CompileParams, "simulate": Input}
INPUT_KINDS = {"compile": [], "simulate": ["component"], "aggregate": ["index"]}


class Refused(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def canonical(model: BaseModel) -> bytes:
    """The payload bytes of a model, as wdf_temporal.converter writes them."""
    document = model.model_dump(mode="json", by_alias=True)
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _read(name: str):
    text = (CONTRACT / name).read_text()
    return json.loads(text) if name.endswith(".json") else yaml.safe_load(text)


@cache
def exit_classes() -> dict[str, tuple[str, bool | str]]:
    """Each class of exit-classes.yaml but ok, with its pool and deterministic value."""
    pools = _read("exit-classes.yaml")["pools"]
    return {
        row["class"]: (pool, row["deterministic"])
        for pool, entry in pools.items()
        for row in entry["classes"]
        if row["class"] != "ok"
    }


@cache
def artifact_codes() -> dict[str, bool]:
    """Each code of failures.yaml with whether a retry fails the same way."""
    return {
        row["code"]: not (
            row["attempt_class"] == "retryable" or row["retry"] == "fallback-to-previous-checkpoint"
        )
        for row in _read("failures.yaml")["codes"]
    }


@cache
def registry_pools() -> set[str]:
    return {entry["pool"] for entry in _read("registry.json")["task_types"].values()}


@cache
def declared_outputs() -> dict[str, list[str]]:
    """The output kinds of each task type of staging.yaml, in order."""
    types = _read("staging.yaml")["task_types"]
    return {name: [out["kind"] for out in task["outputs"]] for name, task in types.items()}


@cache
def statuses() -> set[str]:
    return set(_read("status.yaml")["statuses"])


@cache
def failure_classes() -> dict[str, FailureClass]:
    """The pinned failure class table, keyed by class."""
    table = FailureClasses.model_validate(_read("failure-classes.yaml"))
    return {row.class_: row for row in table.classes}


def naming() -> Naming:
    return Naming.model_validate(_read("naming.yaml"))


def pools() -> Pools:
    return Pools.model_validate(_read("pools.yaml"))


def uncovered_exit_classes(table: FailureClasses) -> list[str]:
    rows = {row.class_ for row in table.classes}
    return sorted(set(exit_classes()) - rows)


def uncovered_artifact_codes(table: FailureClasses) -> list[str]:
    rows = {row.class_ for row in table.classes}
    return sorted(set(artifact_codes()) - rows)


def check_bound(name: str, model: BaseModel) -> None:
    bound = FILES[name][2]
    size = len(canonical(model))
    if bound is not None and size > bound:
        raise Refused("too-large", f"the {name} payload is {size} bytes, the bound is {bound}")


def check_task_input(task: TaskInput) -> None:
    request = task.request
    if request.attempt is not None:
        raise Refused("bad-request", "the request carries attempt, which task.requested refuses")
    try:
        typed = check_task_request(to_bytes(request))
    except (EventError, ValidationError) as error:
        raise Refused("bad-request", " ".join(str(error).split())) from error
    task_type = typed.data.task_type
    shape = type(task.params)
    if task_type in PARAMS:
        matches = shape is PARAMS[task_type]
    else:
        matches = shape not in PARAMS.values()
    if not matches:
        raise Refused(
            "params-type-mismatch",
            f"a {task_type} task has params of the shape {shape.__name__}",
        )
    if task.cache_key is not None and task_type != "compile":
        raise Refused("cache-key-mismatch", f"a {task_type} task carries a cache key")
    kinds = [reference.kind for reference in typed.data.inputs]
    if kinds != INPUT_KINDS[task_type]:
        raise Refused(
            "inputs-mismatch", f"a {task_type} task needs {INPUT_KINDS[task_type]}, has {kinds}"
        )


def check_task_result(result: TaskResult) -> None:
    kinds = [reference.kind for reference in result.outputs]
    if kinds not in declared_outputs().values():
        raise Refused(
            "outputs-not-declared", f"{kinds} are not the declared outputs of one task type"
        )


def check_failure(failure: RaisedFailure) -> None:
    row = failure_classes().get(failure.type)
    if row is None:
        raise Refused("unknown-class", f"{failure.type} is not a row of failure-classes.yaml")
    if row.source not in REPORTED_SOURCES:
        raise Refused(
            "class-not-reportable", f"{failure.type} is a {row.source} class the workflow decides"
        )
    details = failure.details
    if row.deterministic != "from-error-file" and details.deterministic != row.deterministic:
        raise Refused(
            "deterministic-mismatch",
            f"{failure.type} is deterministic {row.deterministic}, the details say "
            f"{details.deterministic}",
        )
    if not details.reason.startswith(f"{failure.type}: "):
        raise Refused("reason-prefix", f"the reason does not start with '{failure.type}: '")


def check_failure_classes(table: FailureClasses) -> None:
    seen: set[str] = set()
    for row in table.classes:
        if row.class_ in seen:
            raise Refused("duplicate-class", f"{row.class_} is listed twice")
        seen.add(row.class_)
    workload, artifact = exit_classes(), artifact_codes()
    for row in table.classes:
        if row.class_ not in workload and row.class_ not in artifact:
            if row.source not in CONTRACT_SOURCES:
                raise Refused(
                    "unknown-class",
                    f"{row.class_} is in neither exit-classes.yaml nor failures.yaml",
                )
    for row in table.classes:
        name = row.class_
        if name in workload and (row.source != "workload" or row.pool != workload[name][0]):
            raise Refused(
                "source-mismatch",
                f"{name} is a workload class of {workload[name][0]}, the row says {row.source} "
                f"{row.pool}",
            )
        if name in artifact and row.source != "artifact":
            raise Refused(
                "source-mismatch", f"{name} is an artifact code, the row says {row.source}"
            )
    for row in table.classes:
        name = row.class_
        source = workload[name][1] if name in workload else artifact.get(name, row.deterministic)
        if row.deterministic != source:
            raise Refused(
                "deterministic-mismatch",
                f"{name} is deterministic {source} in its source, the row says {row.deterministic}",
            )
    for row in table.classes:
        if row.deterministic is True and row.budget != "none":
            raise Refused(
                "deterministic-retried",
                f"{row.class_} is deterministic and has budget {row.budget}",
            )
    for row in table.classes:
        if row.deterministic is not True and row.budget == "none":
            raise Refused("transient-never-retried", f"{row.class_} is never retried")
    for row in table.classes:
        allowed = {
            None: True,
            "escalate-once": row.source == "workload"
            and row.pool in ("compile", "aggregate")
            and row.budget == "guest",
            "invalidate-cache-and-rebuild": row.source == "artifact",
            "previous-checkpoint": row.source == "artifact",
            "re-place": row.source == "temporal",
        }[row.action]
        if not allowed:
            raise Refused("bad-action", f"{row.action} is not an action of a {row.source} row")
    missing = uncovered_exit_classes(table) + uncovered_artifact_codes(table)
    if missing:
        raise Refused("class-not-covered", f"no row for {', '.join(missing)}")


def check_naming(model: Naming) -> None:
    activities = model.activities
    names = [
        model.workflow.queue,
        model.control_queue,
        *model.pools.values(),
        model.schedules["artifact_reaper"].queue,
        *(
            a.name
            for group in (activities.pool, activities.control, activities.local)
            for a in group
        ),
        model.schedules["artifact_reaper"].activity,
        *model.signals,
        *model.queries,
    ]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise Refused("duplicate-name", f"{name} is used twice")
        seen.add(name)
    check_pools(set(model.pools), "naming.yaml")


def check_pools(names: set[str], where: str) -> None:
    if names != registry_pools():
        raise Refused(
            "pool-mismatch", f"{where} has {sorted(names)}, the registry {sorted(registry_pools())}"
        )


def check_status_query(query: StatusQuery) -> None:
    if query.job_status not in statuses():
        raise Refused("unknown-status", f"{query.job_status} is not a status of status.yaml")
    for task in query.tasks:
        if task.last_class is not None and task.last_class not in failure_classes():
            raise Refused(
                "unknown-class", f"{task.last_class} is not a row of failure-classes.yaml"
            )
    if (query.pause_reason is None) == (query.job_status == "paused"):
        raise Refused(
            "pause-reason-mismatch",
            f"pause_reason {query.pause_reason} with job_status {query.job_status}",
        )
    ids = [task.id for task in query.tasks]
    if any(a >= b for a, b in zip(ids, ids[1:], strict=False)):
        raise Refused("out-of-order", "the tasks are not in ascending task id")


CHECKS = {
    "task-input": check_task_input,
    "task-result": check_task_result,
    "failure-details": check_failure,
    "failure-classes": check_failure_classes,
    "naming": check_naming,
    "pools": lambda model: check_pools(set(model.pools), "pools.yaml"),
    "status-query": check_status_query,
}


def name_of(path: Path) -> str | None:
    parts = path.parts
    if "vectors" in parts and parts.index("vectors") + 1 < len(parts) - 1:
        name = parts[parts.index("vectors") + 1]
    else:
        name = path.name.split(".")[0]
    return name if name in FILES else None


def check(path: Path, name: str) -> None:
    """Raises Refused with the first rule that the file of the schema `name` breaks."""
    try:
        text = path.read_bytes().decode()
        document = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise Refused("schema-violation", " ".join(str(error).split())) from error
    model = RaisedFailure if name == "failure-details" else FILES[name][0]
    try:
        parsed = model.model_validate(document)
    except ValidationError as error:
        raise Refused("schema-violation", " ".join(str(error).split())) from error
    check_bound(name, parsed.details if name == "failure-details" else parsed)
    if name in CHECKS:
        CHECKS[name](parsed)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="wdf_workflow.contract", exit_on_error=False)
    parser.add_argument("--check", type=Path, required=True)
    parser.add_argument("--as", dest="name", choices=sorted(FILES))
    try:
        args = parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        return 2
    name = args.name or name_of(args.check)
    if not args.check.is_file() or name is None:
        print(
            "usage: contract --check FILE [--as NAME], NAME one of " + ", ".join(FILES),
            file=sys.stderr,
        )
        return 2
    try:
        check(args.check, name)
    except Refused as refused:
        print(f"reason: {refused}", file=sys.stderr)
        return 1
    print(f"ok {args.check}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
