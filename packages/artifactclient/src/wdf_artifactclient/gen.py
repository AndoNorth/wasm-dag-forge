"""Generates the schemas of the artifact contract files from the models and checks the committed.

Usage: python -m wdf_artifactclient.gen [--check] [--root DIR]
"""

import argparse
import json
import sys
from pathlib import Path

from .models import FILES

REPO_ROOT = Path(__file__).resolve().parents[4]


def render(name: str) -> str:
    model, path = FILES[name]
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:wdf:schema:artifact:{name}:3"
    schema["title"] = f"contracts/artifact {name}.yaml"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_artifactclient.gen")
    parser.add_argument("--check", action="store_true", help="fail when a committed schema differs")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    rc = 0
    for name, (_, relative) in FILES.items():
        path = args.root / relative
        text = render(name)
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        elif not path.is_file() or path.read_text() != text:
            print(f"drift: {relative}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
