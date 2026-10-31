"""Loader of contracts/stages/stages.yaml: python -m wdf_stages.check <stages.yaml>."""

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from wdf_events.models import DATA_MODELS, Event

SCHEMA = Path(__file__).resolve().parents[4] / "contracts/stages/stages.schema.json"
# The task request is built by the workflow and handed to an activity, so no event row records it.
UNRECORDED = "task.requested"

CATALOGUE = {name for name, _ in DATA_MODELS}
ATTRIBUTES = set(Event.model_fields) - {"data"}


class Invalid(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def data_fields(name: str) -> set[str]:
    """The fields of the highest minor the catalogue holds for a type."""
    minor = max(m for t, m in DATA_MODELS if t == name)
    return set(DATA_MODELS[(name, minor)].model_fields)


def check(table: object) -> None:
    """Raises Invalid with the first rule the table breaks."""
    errors = sorted(
        Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(table), key=str
    )
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path)
        raise Invalid("schema-violation", f"{where}: {errors[0].message}"[:300])

    emitters: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for stage, spec in table["stages"].items():
        for attribute in spec["reads"]["attributes"]:
            if attribute not in ATTRIBUTES:
                raise Invalid(
                    "unknown-attribute", f"{stage} reads {attribute}, which is not an attribute"
                )
        for name, fields in spec["reads"]["data"].items():
            if name not in CATALOGUE:
                raise Invalid(
                    "unknown-type", f"{stage} reads data of {name}, which is not in the catalogue"
                )
            for field in fields:
                if field not in data_fields(name):
                    raise Invalid(
                        "unknown-data-field", f"{stage} reads {name}.{field}, which does not exist"
                    )
        for event in spec["emits"]:
            if event["type"] not in CATALOGUE:
                raise Invalid(
                    "unknown-type", f"{stage} emits {event['type']}, which is not in the catalogue"
                )
            if event["source"] != spec["source"]:
                raise Invalid(
                    "source-mismatch",
                    f"{stage} emits {event['type']} from {event['source']}, not {spec['source']}",
                )
            emitters[event["type"]].append((stage, event))

    for name, entries in sorted(emitters.items()):
        if len(entries) > 1 and not all("when" in event for _, event in entries):
            stages = ", ".join(stage for stage, _ in entries)
            raise Invalid(
                "duplicate-emitter", f"{name} is emitted by {stages} without a when on each"
            )
    for name in sorted(CATALOGUE - set(emitters) - {UNRECORDED}):
        raise Invalid("unemitted-type", f"{name} is emitted by no stage")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print("usage: check.sh <stages.yaml>", file=sys.stderr)
        return 2
    try:
        check(yaml.safe_load(Path(argv[0]).read_text()))
    except Invalid as exc:
        print(f"reason: {exc.reason}: {exc.detail}", file=sys.stderr)
        return 1
    print(f"ok {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
