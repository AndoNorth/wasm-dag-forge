"""Task I/O staging: inputs into a scratch directory, declared outputs out, the output tail.

The scratch directory holds `input/`, where the caller writes `input.json` beside the staged
inputs, and `output/`, where the guest writes. The library never writes `input.json`.
"""

import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, BinaryIO, Literal, NamedTuple

import yaml
from wdf_events.models import ProducerRef

from .client import Progress, as_reference
from .errors import OutputMissing
from .keys import CONTENT, PLACEHOLDER, kind_spec
from .models import CONTRACT, Staging

STUBBED = ()

READ_ONLY = 0o444
TAIL_READ = 64 * 1024

Entry = Literal["file", "symlink", "directory", "other"]


@cache
def contract() -> Staging:
    return Staging.model_validate(yaml.safe_load((CONTRACT / "staging.yaml").read_text()))


class Skipped(NamedTuple):
    """An entry of the output directory that is not a declared output, so it was not uploaded."""

    name: str
    entry: Entry

    def __str__(self) -> str:
        return self.name if self.entry == "file" else f"{self.name} ({self.entry})"


@dataclass(frozen=True)
class Staged:
    """The staged inputs: the directory that holds them and the path of each by reference name."""

    directory: Path
    paths: dict[str, Path]


@dataclass(frozen=True)
class Collected:
    """The references of the uploaded outputs and the undeclared entries that were not uploaded."""

    references: list[Any] = field(default_factory=list)
    undeclared: list[Skipped] = field(default_factory=list)


def stage_inputs(
    client: Any,
    inputs: Iterable[Any],
    scratch: Path,
    deadline: float | None = None,
    progress: Progress | None = None,
) -> Staged:
    """Downloads each input reference to `input/<reference name>`, verified and then read only.

    A name that repeats, or that is the file the caller writes itself, is refused before any
    download, because two inputs would otherwise overwrite one another.
    """
    references = [as_reference(reference) for reference in inputs]
    directory = scratch / contract().directories.input
    reserved = contract().input_file
    names = [reference.name for reference in references]
    for name in names:
        if name == reserved or name in (".", "..") or "/" in name:
            raise ValueError(f"input name {name!r} cannot be staged")
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"input names repeat: {repeated}")
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for reference in references:
        path = directory / reference.name
        client.get(reference, path, deadline=deadline, progress=progress)
        path.chmod(READ_ONLY)
        paths[reference.name] = path
    return Staged(directory, paths)


def classify_entry(path: Path) -> Entry:
    mode = path.lstat().st_mode
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    return "other"


def collect_outputs(
    client: Any,
    scratch: Path,
    task_type: str,
    producer: ProducerRef | dict,
    tenant: str,
    batch: str,
    seed: int | str | None = None,
    deadline: float | None = None,
    progress: Progress | None = None,
) -> Collected:
    """Uploads the declared outputs of the task type and lists everything else it found.

    A declared output that is absent or not a regular file fails with `OutputMissing` before
    anything is uploaded, so a doomed attempt leaves no objects behind. A content-addressed kind
    goes up by its hash and an attempt-scoped kind under the key of the producer, and the
    references come back in the order of the contract.
    """
    declared = contract().task_types[task_type].outputs
    directory = scratch / contract().directories.output
    found = {path.name: classify_entry(path) for path in sorted(directory.iterdir())}
    absent = [out.file for out in declared if found.get(out.file) != "file"]
    if absent:
        raise OutputMissing(f"{task_type} did not produce a regular file for {absent}")
    for out in declared:
        size = (directory / out.file).stat().st_size
        if size > out.cap_bytes:
            raise ValueError(f"{out.file} is {size} bytes, above its cap of {out.cap_bytes}")
    references = []
    for out in declared:
        path = directory / out.file
        if kind_spec(out.kind)["class"] == CONTENT:
            reference = client.put_content(
                path, out.kind, out.file, deadline=deadline, progress=progress
            )
        else:
            takes_seed = "seed" in PLACEHOLDER.findall(kind_spec(out.kind)["key"])
            reference = client.put_attempt(
                path,
                out.kind,
                out.file,
                producer,
                tenant,
                batch,
                seed if takes_seed else None,
                deadline=deadline,
                progress=progress,
            )
        references.append(reference)
    names = {out.file for out in declared}
    undeclared = [Skipped(name, entry) for name, entry in found.items() if name not in names]
    return Collected(references, undeclared)


def capture_tail(stream: BinaryIO, cap: int) -> bytes:
    """Reads the stream to its end and returns its last `cap` bytes, holding no more than that."""
    kept = bytearray()
    while chunk := stream.read(TAIL_READ):
        kept += chunk
        if len(kept) > cap:
            del kept[: len(kept) - cap]
    return bytes(kept)


def upload_tail(
    client: Any,
    tail: bytes,
    producer: ProducerRef | dict,
    tenant: str,
    batch: str,
    seed: int | str | None = None,
    undeclared: Iterable[Skipped] = (),
    deadline: float | None = None,
    progress: Progress | None = None,
) -> Any:
    """Uploads the output tail as `log-tail`, which an attempt does even when it failed.

    The undeclared entries of the output directory are listed at the end, and the whole stays
    within the cap of the tail, whose oldest bytes give way.
    """
    spec = contract().log_tail
    listing = "".join(f"\nundeclared: {skipped}" for skipped in undeclared)
    body = (tail + listing.encode())[-spec.cap_bytes :]
    takes_seed = "seed" in PLACEHOLDER.findall(kind_spec(spec.kind)["key"])
    return client.put_attempt(
        body,
        spec.kind,
        spec.name,
        producer,
        tenant,
        batch,
        seed if takes_seed else None,
        deadline=deadline,
        progress=progress,
    )
