"""One results.xml read as parsed rows, refused when it breaks wdf.sim.v1.

Every read is streamed, so a result is held one row at a time whatever its size.
"""

import itertools
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

from wdf_artifactclient.client import kind_caps
from wdf_kernel import reader
from wdf_kernel.digest import digest
from wdf_kernel.format import FormatError, check_document
from wdf_kernel.writer import KINDS


class ParseFailed(ValueError):
    """A result that is not well formed or breaks wdf.sim.v1, naming its task."""


def max_bytes() -> int:
    """The staging cap of results.xml, above which a result is refused unread."""
    return kind_caps()["result"]


def rows(task: str, path: Path) -> Iterator[tuple[str, dict]]:
    """(block id, values) for each row of the result, in file order."""
    try:
        with path.open("rb") as source:
            for block, _, values in reader.rows(source, KINDS, max_bytes()):
                yield block, values
    except FormatError as error:
        raise ParseFailed(f"{task}: {error}") from error


def _outline(path: Path) -> tuple[int, list[tuple[str, str]]]:
    """The seed and the (id, kind) of every block, empty ones too, of a checked document."""
    seed, blocks = None, []
    with path.open("rb") as source:
        for _, element in ET.iterparse(source, events=("start",)):
            if element.tag == "results":
                seed = int(element.get("seed"))
            elif element.tag == "block":
                blocks.append((element.get("id"), element.get("kind")))
            element.clear()
    return seed, blocks


def seed_and_digest(task: str, path: Path) -> tuple[int, str, int]:
    """The seed, the kernel digest over the parsed values and the row count of the result."""
    try:
        with path.open("rb") as source:
            if source.seek(0, 2) > max_bytes():
                raise FormatError("output-over-cap", f"the document is above {max_bytes()} bytes")
            source.seek(0)
            # The format check refuses a DOCTYPE before any parse below can expand an entity.
            check_document(source, KINDS)
    except FormatError as error:
        raise ParseFailed(f"{task}: {error}") from error
    seed, outline = _outline(path)
    count = 0

    def counted(group):
        nonlocal count
        for _, values in group:
            count += 1
            yield values

    def blocks():
        groups = itertools.groupby(rows(task, path), key=lambda row: row[0])
        pending = next(groups, None)
        for block, kind in outline:
            if pending is not None and pending[0] == block:
                yield {"id": block, "kind": kind, "rows": counted(pending[1])}
                pending = next(groups, None)
            else:
                yield {"id": block, "kind": kind, "rows": ()}

    return seed, digest(seed, blocks()), count
