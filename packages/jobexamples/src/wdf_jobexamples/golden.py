"""The golden plans of the examples: bytes and hashes compiled against the slots fixture.

Run `python -m wdf_jobexamples.golden` to rewrite `vectors/plans/1/`, and with `--check` to compare
without writing.
"""

import hashlib
import json
import sys
from pathlib import Path

from wdf_jobcheck import validate
from wdf_jobdoc.profile_files import load_profile
from wdf_plan import bind, canonical_bytes, compile_plan

from . import VECTORS, examples, load

PLANS = VECTORS / "plans/1"
HASHES = PLANS / "HASHES"
PARAMS = VECTORS / "job-params/valid"
REPO_ROOT = VECTORS.parents[2]


def golden_files() -> dict[str, bytes]:
    """The golden file name and canonical bytes of each example's manifest and seed 7 plan."""
    profile = load_profile("ci", REPO_ROOT, VECTORS / "slots-fixture")
    files = {}
    for name in examples():
        report = validate(load(name), profile)
        if not report.ok:
            raise ValueError(f"{name} is not valid: {report.issues[0].code}")
        template = compile_plan(report.document, profile)
        params = json.loads((PARAMS / f"{name}.seed-7.json").read_text())
        files[f"{name}.manifest.json"] = canonical_bytes(template)
        files[f"{name}.seed-7.plan.json"] = canonical_bytes(bind(template, params, profile))
    return files


def hashes_text(files: dict[str, bytes]) -> str:
    """One `sha256sum` style line per file, sorted by name."""
    return "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in sorted(files))


def disagreements(root: Path = PLANS) -> list[str]:
    """What differs between the files of `root` and its HASHES lines, one message each."""
    lines = {}
    for line in (root / "HASHES").read_text().splitlines():
        digest, name = line.split("  ", 1)
        lines[name] = digest
    found = []
    on_disk = {p.name for p in root.glob("*.json")}
    for name in sorted(on_disk - lines.keys()):
        found.append(f"{name} has no HASHES line")
    for name in sorted(lines.keys() - on_disk):
        found.append(f"{name} is listed in HASHES and missing")
    for name in sorted(on_disk & lines.keys()):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != lines[name]:
            found.append(f"{name} does not match its HASHES line")
    return found


def write(root: Path = PLANS) -> None:
    files = golden_files()
    root.mkdir(parents=True, exist_ok=True)
    for stale in root.glob("*.json"):
        stale.unlink()
    for name, data in files.items():
        (root / name).write_bytes(data)
    (root / "HASHES").write_text(hashes_text(files))


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        current = golden_files()
        same = not disagreements() and all(
            (PLANS / n).read_bytes() == b for n, b in current.items()
        )
        sys.exit(0 if same else 1)
    write()
