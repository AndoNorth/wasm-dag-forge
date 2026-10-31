"""Builds the golden events and the poison vectors, and checks the golden manifest.

Usage: python -m wdf_compat.vectors [--check]
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from wdf_events import MAX_BYTES, build_event, to_bytes
from wdf_events.models import DATA_MODELS
from wdf_events.profile import CONTRACT_DIR

VECTORS = CONTRACT_DIR / "vectors"
GOLDEN = VECTORS / "golden"
POISON = VECTORS / "poison"
MANIFEST = "MANIFEST.sha256"


def _fixed_facts(name: str) -> dict:
    """The facts of the contract's valid vector of a type, which stay fixed once pinned."""
    vector = VECTORS / (
        "accepted/valid/accepted.json" if name == "job.accepted" else f"types/{name}/valid.json"
    )
    document = json.loads(vector.read_text())
    return dict(
        source=document["source"],
        tenant=document["tenant"],
        jobid=document["jobid"],
        priorityclass=document["priorityclass"],
        jobclass=document["jobclass"],
        traceparent=document["traceparent"],
        time=datetime.strptime(document["time"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC),
        data=document["data"],
        taskid=document.get("taskid"),
        attempt=document.get("attempt"),
    )


def golden_files() -> dict[str, bytes]:
    """The canonical bytes of one event per type and minor, keyed by path under `golden/`."""
    return {
        f"{name}/{version}.json": to_bytes(build_event(name, **_fixed_facts(name)))
        for name, version in sorted(DATA_MODELS)
    }


def manifest_text(files: dict[str, bytes]) -> str:
    return "".join(f"{hashlib.sha256(files[p]).hexdigest()}  {p}\n" for p in sorted(files))


def check_manifest(directory: Path) -> list[str]:
    """The disagreements between the golden files in `directory` and its manifest."""
    files = {p.relative_to(directory).as_posix() for p in directory.glob("*/*.json")}
    lines = {}
    for line in (directory / MANIFEST).read_text().splitlines():
        digest, _, path = line.partition("  ")
        lines[path] = digest
    problems = [f"{p}: file has no line" for p in sorted(files - lines.keys())]
    problems += [f"{p}: line has no file" for p in sorted(lines.keys() - files)]
    for path in sorted(files & lines.keys()):
        if hashlib.sha256((directory / path).read_bytes()).hexdigest() != lines[path]:
            problems.append(f"{path}: hash differs")
    return problems


def poison_files() -> dict[str, tuple[bytes, dict[str, str]]]:
    """One poison input per failure, with the typed error `read_event` must raise for it."""
    base = json.loads(golden_files()["job.accepted/1.0.json"])

    def encode(document: dict) -> bytes:
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    def without(key: str) -> dict:
        return {k: v for k, v in base.items() if k != key}

    wrong_major = {
        **base,
        "type": "dev.wdf.job.accepted.v2",
        "dataschema": "urn:wdf:schema:job.accepted:2.0",
    }
    bad_data = {**base, "data": {k: v for k, v in base["data"].items() if k != "batch_id"}}
    padding = b" " * (MAX_BYTES + 1 - len(encode(base)))
    return {
        "wrong-major": (
            encode(wrong_major),
            dict(error="UnsupportedMajor", reason="unsupported-major"),
        ),
        "schema-invalid-data": (encode(bad_data), dict(error="SchemaInvalid", reason="bad-data")),
        "missing-time": (
            encode(without("time")),
            dict(error="SchemaInvalid", reason="missing-time"),
        ),
        "id-with-hash": (
            encode({**base, "id": "a#b"}),
            dict(error="SchemaInvalid", reason="id-contains-hash"),
        ),
        "not-json": (b"this is not json", dict(error="SchemaInvalid", reason="malformed-json")),
        "truncated-json": (
            encode(base)[:100],
            dict(error="SchemaInvalid", reason="malformed-json"),
        ),
        "datacontenttype-other": (
            encode({**base, "datacontenttype": "text/plain"}),
            dict(error="SchemaInvalid", reason="bad-datacontenttype"),
        ),
        "over-64kib": (
            encode(base) + padding,
            dict(error="EventTooLarge", reason="event-too-large"),
        ),
    }


def render() -> dict[Path, bytes]:
    """Every file this module owns, by absolute path."""
    golden = golden_files()
    out = {GOLDEN / p: b for p, b in golden.items()}
    out[GOLDEN / MANIFEST] = manifest_text(golden).encode()
    for name, (data, expect) in poison_files().items():
        out[POISON / f"{name}.json"] = data
        out[POISON / f"{name}.expect.json"] = (
            json.dumps(expect, sort_keys=True, indent=2) + "\n"
        ).encode()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vectors")
    parser.add_argument("--check", action="store_true", help="fail when a committed file differs")
    args = parser.parse_args(argv)
    drift = []
    for path, content in render().items():
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                drift.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    for path in drift:
        print(f"drift: {path}", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
