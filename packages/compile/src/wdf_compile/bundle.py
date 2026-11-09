"""The bundle format: an uncompressed tar of pyproject.toml, uv.lock and the source modules."""

from pathlib import Path

from wdf_jobcheck.resolver import BundleInfo

from ._stub import not_implemented

STUBBED = ("info", "write")


def info(path: Path) -> BundleInfo:
    raise not_implemented("C")


def write(tree: Path, out: Path) -> Path:
    raise not_implemented("C")
