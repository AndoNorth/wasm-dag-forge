"""Generates contracts/events/schemas and the artifact reference schema from the data models.

Usage: python -m wdf_events.schemagen [--check] [--root DIR]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, get_args

from .models import DATA_MODELS, AttemptReference, ContentReference, ProducerRef

REPO_ROOT = Path(__file__).resolve().parents[4]


def render(name: str, version: str) -> str:
    schema = DATA_MODELS[(name, version)].model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:wdf:schema:{name}:{version}"
    schema["title"] = f"{name} {version}"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


REFERENCE_SCHEMA = Path("contracts/artifact/reference.schema.json")


def _untitled(schema: Any) -> Any:
    """Drops the titles that pydantic derives from class and field names, which carry nothing."""
    if isinstance(schema, dict):
        return {
            k: _untitled(v) for k, v in schema.items() if k != "title" or not isinstance(v, str)
        }
    if isinstance(schema, list):
        return [_untitled(v) for v in schema]
    return schema


def render_reference() -> str:
    """The flat reference schema, with a producer required for the attempt-scoped kinds only."""
    fields = ContentReference.model_json_schema()["properties"]
    producer = _untitled(ProducerRef.model_json_schema())
    producer["properties"]["attempt"]["description"] = (
        "An attempt identity as the key formulas define it."
    )
    scoped = list(get_args(AttemptReference.model_fields["kind"].annotation))
    kinds = [*get_args(ContentReference.model_fields["kind"].annotation), *scoped]
    properties = {k: _untitled(v) for k, v in fields.items() if k != "kind"}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Artifact reference",
        "description": "A pointer to an object in a bucket. It never holds bytes, and the consumer "
        "verifies sha256 and size against the object.",
        "type": "object",
        "additionalProperties": False,
        "required": [*properties, "kind"],
        "properties": {
            **properties,
            "kind": {"enum": kinds},
            "producer": {
                "description": "The attempt that wrote the object, present for the attempt-scoped "
                "kinds only.",
                **producer,
            },
        },
        "if": {"required": ["kind"], "properties": {"kind": {"enum": scoped}}},
        "then": {"required": ["producer"]},
        "else": {"not": {"required": ["producer"]}},
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def schema_path(root: Path, name: str, version: str) -> Path:
    return root / "contracts/events/schemas" / name / f"{version}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schemagen")
    parser.add_argument(
        "--check", action="store_true", help="fail when a committed schema differs from its model"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    drift = []
    targets = [(schema_path(args.root, *key), render(*key)) for key in sorted(DATA_MODELS)]
    targets.append((args.root / REFERENCE_SCHEMA, render_reference()))
    for path, text in targets:
        if args.check:
            if not path.is_file() or path.read_text() != text:
                drift.append(path.relative_to(args.root))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if args.check:
        # A schema file with no model would otherwise live on after its model is removed.
        known = {schema_path(args.root, *key) for key in DATA_MODELS}
        for path in sorted((args.root / "contracts/events/schemas").glob("*/*.json")):
            if path not in known:
                drift.append(path.relative_to(args.root))
        for path in drift:
            print(f"drift: {path}", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
