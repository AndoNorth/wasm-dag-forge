"""Usage: python -m wdf_workloads.bundle --out DIR

Writes DIR/bundle.tar with wdf_kernel, wdf_workloads, a pyproject.toml and a uv.lock with no
dependencies.
"""

import argparse
import io
import sys
import tarfile
from pathlib import Path

import wdf_kernel

import wdf_workloads

STUBBED = ()

NAME = "bundle.tar"
PACKAGES = (wdf_kernel, wdf_workloads)
PYPROJECT = b"""\
[project]
name = "wdf-workloads"
version = "0"
requires-python = ">=3.13"
dependencies = []
"""
# What `uv lock` writes for PYPROJECT, kept as bytes so the bundle never depends on the uv at hand.
LOCKFILE = b"""\
version = 1
revision = 3
requires-python = ">=3.13"

[[package]]
name = "wdf-workloads"
version = "0"
source = { virtual = "." }
"""


def files() -> dict[str, bytes]:
    """Every file of the bundle by its path in the archive: the source modules of each package."""
    found = {"pyproject.toml": PYPROJECT, "uv.lock": LOCKFILE}
    for package in PACKAGES:
        root = Path(package.__file__).parent
        for path in root.rglob("*.py"):
            found[path.relative_to(root.parent).as_posix()] = path.read_bytes()
    return found


def write(out: Path) -> Path:
    """Writes out/bundle.tar sorted, with zero times and owners, so equal sources give equal
    bytes."""
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out / NAME, "w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in sorted(files().items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), 0o644
            tar.addfile(entry, io.BytesIO(data))
    return out / NAME


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m wdf_workloads.bundle")
    parser.add_argument("--out", type=Path, required=True)
    write(parser.parse_args(argv).out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
