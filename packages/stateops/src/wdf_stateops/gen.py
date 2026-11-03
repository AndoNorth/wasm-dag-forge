"""Generates contracts/state/inputs.schema.json from the models and checks the committed file.

Usage: python -m wdf_stateops.gen [--check] [--root DIR]
"""

import argparse
import json
import sys
from pathlib import Path

from .inputs import Inputs

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA = Path("contracts/state/inputs.schema.json")


def render() -> str:
    schema = Inputs.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:wdf:schema:state.inputs:1"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_stateops.gen")
    parser.add_argument(
        "--check", action="store_true", help="fail when the committed schema differs"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    path = args.root / SCHEMA
    text = render()
    if not args.check:
        path.write_text(text)
    elif not path.is_file() or path.read_text() != text:
        print(f"drift: {SCHEMA}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
