"""Generates contracts/job from the models and checks the committed files.

Usage: python -m wdf_jobdoc.schemagen [--check] [--root DIR]
"""

import argparse
import json
import sys
from pathlib import Path

from .models import Document
from .plan_models import Plan, PlanStats
from .profile import LimitsFile
from .registry import REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_DIR = "contracts/job/schemas"
# One model per (name, version), written to schemas/<name>/<version>.json.
DOCUMENT_MODELS = {("job", "1.0"): Document, ("plan", "1"): Plan, ("stats", "1"): PlanStats}


def render_document_schema(name: str, version: str) -> str:
    schema = DOCUMENT_MODELS[(name, version)].model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:wdf:schema:{name}:{version}"
    schema["title"] = f"{name} {version}"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_limits_schema() -> str:
    schema = LimitsFile.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:wdf:schema:job-limits:1"
    schema["title"] = "job limits 1"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def outputs() -> dict[str, str]:
    """Every generated file, by repository path."""
    files = {
        f"{SCHEMA_DIR}/{name}/{version}.json": render_document_schema(name, version)
        for name, version in DOCUMENT_MODELS
    }
    files["contracts/job/registry.json"] = json.dumps(REGISTRY, indent=2, sort_keys=True) + "\n"
    files["contracts/job/limits.schema.json"] = render_limits_schema()
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schemagen")
    parser.add_argument(
        "--check", action="store_true", help="fail when a committed file differs from its model"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    files = outputs()
    drift = []
    for rel, text in sorted(files.items()):
        path = args.root / rel
        if args.check:
            if not path.is_file() or path.read_text() != text:
                drift.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if args.check:
        # A schema file with no model would otherwise live on after its model is removed.
        for path in sorted((args.root / SCHEMA_DIR).glob("**/*.json")):
            if (rel := str(path.relative_to(args.root))) not in files:
                drift.append(rel)
        for rel in drift:
            print(f"drift: {rel}", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
