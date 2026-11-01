"""Generates the event row schema and the retention schema, and checks the committed files.

Usage: python -m wdf_eventstore.gen [--check] [--root DIR]
"""

import argparse
import json
import sys
from pathlib import Path

from .models import EventRow, Retention

REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUTS = {
    Path("contracts/state/schemas/event-row/1.json"): (
        EventRow,
        "urn:wdf:schema:state.event-row:1",
    ),
    Path("contracts/state/retention.schema.json"): (Retention, "urn:wdf:schema:state.retention:1"),
}


def render(model, schema_id: str) -> str:
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = schema_id
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_eventstore.gen")
    parser.add_argument("--check", action="store_true", help="fail when a committed file differs")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    drift = []
    for relative, (model, schema_id) in OUTPUTS.items():
        path = args.root / relative
        text = render(model, schema_id)
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        elif not path.is_file() or path.read_text() != text:
            drift.append(relative)
    for relative in drift:
        print(f"drift: {relative}", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
