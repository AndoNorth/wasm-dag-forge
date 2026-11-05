"""Loads one artifact contract file and checks it against the models, the layout and the registry.

Usage: python -m wdf_artifactclient.contract --check <contract file or index.jsonl>
Exit 0 valid, 1 invalid with "reason: <code>: <detail>" on stderr, 2 usage.
"""

import json
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError
from wdf_compat.window import MAX_JOB_DURATION_DAYS

from .models import CONTRACT, Failures, IndexEntry, IndexHeader, Retention, Staging

TOP_LEVEL = {"codes": Failures, "task_types": Staging, "statuses": Retention}


class Refused(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")


def layout_kinds() -> set[str]:
    return set(yaml.safe_load((CONTRACT / "layout.yaml").read_text())["kinds"])


def registry_outputs() -> dict[str, list[str]]:
    """The artifact kinds each task type of contracts/job/registry.json declares as outputs."""
    types = json.loads((CONTRACT / "registry.json").read_text())["task_types"]
    return {name: sorted(entry["outputs"].values()) for name, entry in types.items()}


def failure_codes() -> set[str]:
    return {
        entry["code"] for entry in yaml.safe_load((CONTRACT / "failures.yaml").read_text())["codes"]
    }


def check_failures(model: Failures) -> None:
    seen: set[str] = set()
    for entry in model.codes:
        if entry.code in seen:
            raise Refused("duplicate-code", f"{entry.code} is listed twice")
        seen.add(entry.code)
        if not entry.surface.event_reason.startswith(f"{entry.code}: "):
            raise Refused(
                "schema-violation",
                f"{entry.code} has the event reason prefix {entry.surface.event_reason!r}",
            )
    for entry in model.codes:
        if entry.escalates_to is not None and entry.escalates_to not in seen:
            raise Refused("unknown-code", f"{entry.code} escalates to {entry.escalates_to}")


def check_staging(model: Staging) -> None:
    kinds = layout_kinds()
    for kind in model.kind_caps:
        if kind not in kinds:
            raise Refused("unknown-kind", f"{kind} is not a kind of layout.yaml")
    for kind in sorted(kinds):
        if kind not in model.kind_caps:
            raise Refused("kind-without-cap", f"{kind} has no entry in kind_caps")

    declared = [(model.log_tail.kind, model.log_tail.cap_bytes, "log_tail")]
    for name, task in model.task_types.items():
        declared += [(out.kind, out.cap_bytes, f"{name} output {out.file}") for out in task.outputs]
    for kind, cap, where in declared:
        if kind not in kinds:
            raise Refused(
                "unknown-kind", f"{where} names {kind}, which is not a kind of layout.yaml"
            )
        if cap > model.kind_caps[kind]:
            raise Refused(
                "cap-above-kind-cap",
                f"{where} caps at {cap}, above {model.kind_caps[kind]} for {kind}",
            )

    if model.missing_output not in failure_codes():
        raise Refused("unknown-code", f"missing_output names {model.missing_output}")

    registry = registry_outputs()
    for name in model.task_types:
        if name not in registry:
            raise Refused("unknown-task-type", f"{name} is not a task type of the registry")
    for name, want in registry.items():
        if name not in model.task_types:
            raise Refused(
                "output-not-in-registry", f"the registry declares {name}, staging does not"
            )
        have = sorted(out.kind for out in model.task_types[name].outputs)
        if have != want:
            raise Refused(
                "output-not-in-registry", f"{name} declares {have}, the registry declares {want}"
            )
        files = [out.file for out in model.task_types[name].outputs]
        if len(set(files)) != len(files):
            raise Refused("schema-violation", f"{name} declares a file name twice")


def terminal_statuses() -> set[str]:
    return set(yaml.safe_load((CONTRACT / "status.yaml").read_text())["terminal"])


def safety_net_days() -> dict[str, int]:
    """The days after which the lifecycle rule of the store removes the objects of each bucket."""
    windows = yaml.safe_load((CONTRACT / "state-retention.yaml").read_text())["windows"]
    return {
        bucket: rule["expire_after_days"]
        for bucket, rule in windows.items()
        if "expire_after_days" in rule
    }


def check_retention(model: Retention) -> None:
    terminal = terminal_statuses()
    for status in model.statuses:
        if status not in terminal:
            raise Refused("unknown-status", f"{status} is not a terminal status of status.yaml")
    for status in sorted(terminal - set(model.statuses)):
        raise Refused("schema-violation", f"the terminal status {status} has no window")

    net = safety_net_days()
    for status, window in model.statuses.items():
        for bucket, days in (
            ("wdf-results", window.results_days),
            ("wdf-checkpoints", window.checkpoints_days),
        ):
            if days > net[bucket]:
                raise Refused(
                    "window-above-safety-net",
                    f"{status} keeps {bucket} for {days} days, above the net of {net[bucket]}",
                )

    floor = 2 * MAX_JOB_DURATION_DAYS
    if model.cache_unused_days < floor:
        raise Refused(
            "cache-window-below-twice-max-job-duration",
            f"the cache window is {model.cache_unused_days} days, below {floor}",
        )
    if model.checkpoint_keep < 2:
        raise Refused("keep-below-two", f"checkpoint_keep is {model.checkpoint_keep}")

    base = model.statuses.get("succeeded")
    for status in ("failed", "partial"):
        window = model.statuses.get(status)
        if (
            base
            and window
            and (
                window.results_days < base.results_days
                or window.checkpoints_days < base.checkpoints_days
            )
        ):
            raise Refused(
                "failed-shorter-than-succeeded",
                f"{status} keeps objects for less time than succeeded",
            )


def canonical(value) -> str:
    """The one serialization of a line of an index object, so equal entries give equal bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def check_index(text: str) -> None:
    """Raises Refused with the first rule that an aggregate index object breaks."""
    lines = text.split("\n")
    if lines[-1] != "":
        raise Refused("schema-violation", "the last line has no line feed")
    lines.pop()
    previous = None
    for number, line in enumerate(lines, start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise Refused("schema-violation", f"line {number} is not JSON: {error}") from error
        is_header = isinstance(value, dict) and "schema" in value
        if is_header != (number == 1):
            raise Refused(
                "header-not-first", f"line {number} is {'a header' if is_header else 'an entry'}"
            )
        try:
            parsed = (IndexHeader if is_header else IndexEntry).model_validate(value)
        except ValidationError as error:
            raise Refused(
                "schema-violation", f"line {number}: " + " ".join(str(error).split())
            ) from error
        if canonical(value) != line:
            raise Refused("schema-violation", f"line {number} is not canonical JSON")
        if is_header:
            continue
        if parsed.status == "succeeded" and parsed.reference is None:
            raise Refused(
                "entry-without-reference", f"{parsed.task} succeeded and has no reference"
            )
        if parsed.status != "succeeded" and parsed.reference is not None:
            raise Refused(
                "reference-with-failure-status",
                f"{parsed.task} is {parsed.status} and has a reference",
            )
        if parsed.reference is not None and parsed.reference.kind != "result":
            raise Refused(
                "wrong-kind", f"{parsed.task} references a {parsed.reference.kind}, not a result"
            )
        if previous is not None and parsed.task <= previous:
            raise Refused("out-of-order", f"{parsed.task} follows {previous}")
        previous = parsed.task
    if not lines:
        raise Refused("header-not-first", "the object has no header")


def check(document) -> None:
    """Raises Refused with the first rule that the document breaks."""
    model: type[BaseModel] | None = None
    if isinstance(document, dict):
        model = next((m for key, m in TOP_LEVEL.items() if key in document), None)
    if model is None:
        raise Refused(
            "schema-violation", "the file has neither codes nor task_types at its top level"
        )
    try:
        parsed = model.model_validate(document)
    except ValidationError as error:
        raise Refused("schema-violation", " ".join(str(error).split())) from error
    {Failures: check_failures, Staging: check_staging, Retention: check_retention}[model](parsed)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--check" or not Path(argv[1]).is_file():
        print(
            "usage: contract --check <failures.yaml|staging.yaml|retention.yaml|index.jsonl>",
            file=sys.stderr,
        )
        return 2
    try:
        if argv[1].endswith(".jsonl"):
            check_index(Path(argv[1]).read_text())
        else:
            check(yaml.safe_load(Path(argv[1]).read_text()))
    except yaml.YAMLError as error:
        print(f"reason: schema-violation: {' '.join(str(error).split())}", file=sys.stderr)
        return 1
    except Refused as refused:
        print(f"reason: {refused}", file=sys.stderr)
        return 1
    print(f"ok {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
