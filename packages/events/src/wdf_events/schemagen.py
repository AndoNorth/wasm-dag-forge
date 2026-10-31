"""Generates contracts/events/schemas from the data models and checks the committed files.

Usage: python -m wdf_events.schemagen [--check] [--root DIR]
"""

import argparse
import json
import sys
from pathlib import Path

from .models import DATA_MODELS

REPO_ROOT = Path(__file__).resolve().parents[4]


def render(name: str, version: str) -> str:
    schema = DATA_MODELS[(name, version)].model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:wdf:schema:{name}:{version}"
    schema["title"] = f"{name} {version}"
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
    for name, version in sorted(DATA_MODELS):
        path = schema_path(args.root, name, version)
        text = render(name, version)
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
