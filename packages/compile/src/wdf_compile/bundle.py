"""The bundle format: an uncompressed tar of pyproject.toml, uv.lock and the source modules."""

import hashlib
import os
import tarfile
from pathlib import Path, PurePosixPath

import yaml
from wdf_jobcheck.resolver import BundleInfo
from wdf_runtime.models import CONTRACT

STUBBED = ()

LOCKFILE = "uv.lock"
NAME = "bundle.tar"
SOURCE_CAP = yaml.safe_load((CONTRACT / "staging.yaml").read_text())["kind_caps"]["source"]
MAX_ENTRIES = 10_000
MAX_UNPACKED_BYTES = 256 * 1024 * 1024


class Refused(ValueError):
    """A bundle that breaks the bundle format."""


def info(path: Path) -> BundleInfo:
    """The hash, lockfile hash and modules of a stored bundle, for the dispatch resolver."""
    with _open(path) as tar:
        members = _members(tar)
        lockfile = next((m for m in members if _name(m) == LOCKFILE and m.isfile()), None)
        lockfile_sha256 = None
        if lockfile is not None:
            lockfile_sha256 = hashlib.sha256(tar.extractfile(lockfile).read()).hexdigest()
    with open(path, "rb") as f:
        source_sha256 = hashlib.file_digest(f, "sha256").hexdigest()
    return BundleInfo(
        source_sha256=source_sha256,
        lockfile_sha256=lockfile_sha256,
        modules=frozenset(filter(None, (_module(m) for m in members))),
    )


def unpack(path: Path, dest: Path) -> None:
    with _open(path) as tar:
        tar.extractall(dest, members=_members(tar), filter="data")


def write(tree: Path, out: Path) -> Path:
    """Writes out/bundle.tar from tree, sorted with zero times and owners, so equal trees give equal
    bytes. Bytecode caches are left out, since they hold build times."""
    out.mkdir(parents=True, exist_ok=True)
    paths = sorted(
        (p for p in tree.rglob("*") if "__pycache__" not in p.relative_to(tree).parts),
        key=lambda p: p.relative_to(tree).as_posix(),
    )
    with tarfile.open(out / NAME, "w", format=tarfile.PAX_FORMAT) as tar:
        for path in paths:
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise Refused(f"{path} is not a regular file or a directory")
            entry = tar.gettarinfo(path, path.relative_to(tree).as_posix())
            entry.mtime = entry.uid = entry.gid = 0
            entry.uname = entry.gname = ""
            entry.mode = 0o755 if path.is_dir() or os.access(path, os.X_OK) else 0o644
            if path.is_dir():
                tar.addfile(entry)
            else:
                with open(path, "rb") as f:
                    tar.addfile(entry, f)
    return out / NAME


def _open(path: Path) -> tarfile.TarFile:
    size = Path(path).stat().st_size
    if size > SOURCE_CAP:
        raise Refused(f"the bundle is {size} bytes, above the source cap of {SOURCE_CAP}")
    try:
        return tarfile.open(path, "r:")
    except tarfile.TarError as error:
        raise Refused(f"the bundle is not an uncompressed tar archive: {error}") from error


def _members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members, unpacked = [], 0
    try:
        for member in tar:
            if len(members) == MAX_ENTRIES:
                raise Refused(f"the bundle holds more than {MAX_ENTRIES} entries")
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise Refused(f"{member.name!r} leaves the bundle root")
            if not (member.isfile() or member.isdir()):
                raise Refused(f"{member.name!r} is a link or a device")
            unpacked += member.size
            if unpacked > MAX_UNPACKED_BYTES:
                raise Refused(f"the bundle unpacks to more than {MAX_UNPACKED_BYTES} bytes")
            members.append(member)
    except tarfile.TarError as error:
        raise Refused(f"the bundle is not a well-formed tar archive: {error}") from error
    return members


def _name(member: tarfile.TarInfo) -> str:
    return PurePosixPath(member.name).as_posix()


def _module(member: tarfile.TarInfo) -> str | None:
    """The dotted name of a .py file, where a package is named by its __init__.py."""
    path = PurePosixPath(member.name)
    if not member.isfile() or path.suffix != ".py":
        return None
    parts = path.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if parts and all(part.isidentifier() for part in parts):
        return ".".join(parts)
    return None
