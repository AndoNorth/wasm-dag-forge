"""Checks a decoded event against the CloudEvents schema, the profile and its data schema."""

import copy
import json
import re
from datetime import datetime
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from jsonschema.validators import validator_for

from .errors import SchemaInvalid, UnsupportedMajor

# The contract files the runtime reads ship inside the package, as links to the repository files.
CONTRACT = resources.files(__package__) / "contract"
# The repository copy of the whole contract, with its vectors, for tests and tooling only.
_PARENTS = Path(__file__).resolve().parents
CONTRACT_DIR = (_PARENTS[4] if len(_PARENTS) > 4 else Path()) / "contracts/events"
TYPE_PREFIX = "dev.wdf."
TYPE_RE = re.compile(r"^dev\.wdf\.(?P<name>(?:job|task)(?:\.[a-z0-9]+)+)\.v(?P<major>[1-9][0-9]*)$")
DATASCHEMA_RE = re.compile(
    r"^urn:wdf:schema:(?P<name>[a-z0-9]+(?:\.[a-z0-9]+)*):(?P<major>[0-9]+)\.(?P<minor>[0-9]+)$"
)


@cache
def _profile_validators() -> tuple[Draft7Validator, Draft7Validator]:
    cloudevents = json.loads((CONTRACT / "profile/cloudevents.json").read_text())
    profile = json.loads((CONTRACT / "profile/profile.schema.json").read_text())
    return Draft7Validator(cloudevents), Draft7Validator(profile)


def known_versions(name: str, schemas_dir: Traversable | None = None) -> list[tuple[int, int]]:
    """The (major, minor) pairs that have a schema file for a type name, sorted ascending."""
    directory = (schemas_dir or CONTRACT / "schemas") / name
    pairs = []
    if directory.is_dir():
        for path in directory.iterdir():
            stem, dot, suffix = path.name.rpartition(".")
            if suffix == "json":
                major, _, minor = stem.partition(".")
                pairs.append((int(major), int(minor)))
    return sorted(pairs)


def _reason(error) -> str:
    if error.validator == "required":
        missing = [key for key in error.validator_value if key not in error.instance]
        return f"missing-{missing[0]}"
    schema = error.schema
    if "x-reason" in schema:
        return schema["x-reason"]
    return f"bad-{error.path[0]}" if error.path else "bad-event"


def _loosen(schema: Any) -> Any:
    """A copy of a schema that tolerates added fields, for a minor newer than the reader knows."""
    schema = copy.deepcopy(schema)
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("additionalProperties") is False:
                del node["additionalProperties"]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return schema


def check_event(event: Any, schemas_dir: Traversable | None = None) -> tuple[str, int, int]:
    """Validates a decoded event and returns the (name, major, minor) its data was checked against.

    Raises UnsupportedMajor for a major with no schema and SchemaInvalid for every other failure.
    `schemas_dir` replaces the contract's schema directory, for a registry other than the catalogue.
    """
    if not isinstance(event, dict):
        raise SchemaInvalid(
            "not-an-object", f"an event is a JSON object, got {type(event).__name__}"
        )
    cloudevents, profile = _profile_validators()
    for validator, code in ((cloudevents, "cloudevents-schema"), (profile, None)):
        error = next(
            iter(sorted(validator.iter_errors(event), key=lambda e: list(map(str, e.path)))), None
        )
        if error:
            path = "/".join(map(str, error.path)) or "(event)"
            raise SchemaInvalid(code or _reason(error), f"{path}: {error.message}")

    try:
        datetime.strptime(event["time"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise SchemaInvalid("bad-time", str(exc)) from None

    kind = TYPE_RE.fullmatch(event["type"])
    ds = DATASCHEMA_RE.fullmatch(event["dataschema"])
    if not ds:
        raise SchemaInvalid("bad-dataschema", event["dataschema"])
    name, major = kind["name"], int(kind["major"])

    schemas_dir = schemas_dir or CONTRACT / "schemas"
    versions = known_versions(name, schemas_dir)
    if not versions:
        raise SchemaInvalid("unknown-type", event["type"])
    if major not in {m for m, _ in versions}:
        raise UnsupportedMajor(
            "unsupported-major", f"{event['type']} has no schema for major {major}"
        )
    if ds["name"] != name or int(ds["major"]) != major:
        raise SchemaInvalid(
            "dataschema-type-mismatch", f"{event['dataschema']} does not belong to {event['type']}"
        )

    expected = (
        event["jobid"]
        if kind["name"].startswith("job")
        else f"{event['jobid']}/{event.get('taskid')}"
    )
    if event["subject"] != expected:
        raise SchemaInvalid("bad-subject", f"expected {expected}, got {event['subject']}")

    minor = int(ds["minor"])
    known = (major, minor) in versions
    if not known:
        minor = max(m for ma, m in versions if ma == major)
    schema = json.loads((schemas_dir / name / f"{major}.{minor}.json").read_text())
    if not known:
        schema = _loosen(schema)
    cls = validator_for(schema)
    error = next(
        iter(sorted(cls(schema).iter_errors(event["data"]), key=lambda e: list(map(str, e.path)))),
        None,
    )
    if error:
        path = "/".join(["data", *map(str, error.path)])
        raise SchemaInvalid("bad-data", f"{path}: {error.message}")
    return name, major, minor
