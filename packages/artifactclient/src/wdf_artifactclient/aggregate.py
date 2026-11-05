"""The aggregate index object: canonical JSON Lines of the results that an aggregate task reads.

The builder and the reader hold one entry at a time and work on streams, so an index of a hundred
thousand entries needs no list in memory.
A reader never lists a bucket: it opens the index by its reference and each result by its own.
"""

import contextlib
import json
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, ValidationError
from wdf_events.models import ContentReference

from .client import ArtifactClient, Progress, as_reference
from .contract import Refused, canonical
from .errors import ArtifactLost
from .keys import bucket_of
from .models import IndexEntry, IndexHeader

STUBBED = ()
SCHEMA = "wdf.artifact.index/1"
INDEX_NAME = "index.jsonl"


def _model[M: BaseModel](model: type[M], value, **defaults) -> M:
    return value if isinstance(value, model) else model.model_validate({**defaults, **value})


def _line(model: BaseModel) -> bytes:
    dumped = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return (canonical(dumped) + "\n").encode()


def check_entry(entry: IndexEntry, previous: str | None) -> None:
    """Raises Refused with the rule of the index format that the entry breaks."""
    reference = entry.reference
    if entry.status == "succeeded" and reference is None:
        raise Refused("entry-without-reference", f"{entry.task} succeeded and has no reference")
    if entry.status != "succeeded" and reference is not None:
        raise Refused(
            "reference-with-failure-status", f"{entry.task} is {entry.status} and has a reference"
        )
    if reference is not None and reference.kind != "result":
        raise Refused("wrong-kind", f"{entry.task} references a {reference.kind}, not a result")
    if previous is not None and entry.task <= previous:
        raise Refused("out-of-order", f"{entry.task} follows {previous}")


def build_index(entries: Iterable[IndexEntry | dict], header: IndexHeader | dict, out: BinaryIO):
    """Writes the header and then the entries, which must come in ascending task id order."""
    out.write(_line(_model(IndexHeader, header, schema=SCHEMA)))
    previous = None
    for item in entries:
        entry = _model(IndexEntry, item)
        check_entry(entry, previous)
        out.write(_line(entry))
        previous = entry.task


def put_index(
    client: ArtifactClient,
    entries: Iterable[IndexEntry | dict],
    header: IndexHeader | dict,
    *,
    deadline: float | None = None,
    progress: Progress | None = None,
) -> ContentReference:
    """Builds the index in a scratch file and uploads it. The caller commits the reference."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / INDEX_NAME
        with path.open("wb") as out:
            build_index(entries, header, out)
        return client.put_content(path, "index", INDEX_NAME, deadline=deadline, progress=progress)


def _parse(line: bytes, number: int) -> IndexHeader | IndexEntry:
    if not line:
        raise Refused("header-not-first", "the object has no header")
    try:
        value = json.loads(line)
    except ValueError as error:
        raise Refused("schema-violation", f"line {number} is not JSON: {error}") from error
    is_header = isinstance(value, dict) and "schema" in value
    if is_header != (number == 1):
        raise Refused(
            "header-not-first", f"line {number} is {'a header' if is_header else 'an entry'}"
        )
    try:
        return (IndexHeader if is_header else IndexEntry).model_validate(value)
    except ValidationError as error:
        raise Refused(
            "schema-violation", f"line {number}: " + " ".join(str(error).split())
        ) from error


def read_index(
    client: ArtifactClient,
    reference: ContentReference | dict,
    tenant: str,
    *,
    deadline: float | None = None,
    progress: Progress | None = None,
) -> Iterator[IndexEntry]:
    """Streams the entries of an index object after the client verified its bytes.

    Refuses an index of another tenant, a result outside the tenant's prefix, and an entry that
    did not succeed unless the header allows partial results.
    """
    ref = as_reference(reference)
    if ref.kind != "index":
        raise Refused("wrong-kind", f"{ref.uri} is a {ref.kind}, not an index")
    results = f"s3://{bucket_of('result')}/{tenant}/"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / INDEX_NAME
        client.get(ref, path, deadline=deadline, progress=progress)
        with path.open("rb") as stream:
            header = _parse(stream.readline(), 1)
            if header.tenant != tenant:
                raise Refused("foreign-tenant", f"the index is of {header.tenant}, not {tenant}")
            previous = None
            for number, line in enumerate(stream, start=2):
                entry = _parse(line, number)
                check_entry(entry, previous)
                if entry.status != "succeeded" and header.partial == "refuse":
                    raise Refused(
                        "partial-refused", f"{entry.task} is {entry.status} and the index refuses"
                    )
                if entry.reference is not None and not entry.reference.uri.startswith(results):
                    raise Refused(
                        "foreign-tenant", f"{entry.task} reads {entry.reference.uri}, not {results}"
                    )
                previous = entry.task
                yield entry


def open_results(
    client: ArtifactClient,
    entries: Iterable[IndexEntry],
    scratch: Path,
    *,
    deadline: float | None = None,
    progress: Progress | None = None,
) -> Iterator[tuple[IndexEntry, Path | None]]:
    """Fetches the result of each entry into `scratch` and yields the entry with its file.

    The file is verified and stays until the iterator moves on, so the disk holds one result at a
    time. An entry that did not succeed is yielded with no file.
    """
    current: Path | None = None
    try:
        for entry in entries:
            if current is not None:
                current.unlink(missing_ok=True)
                current = None
            if entry.reference is None:
                yield entry, None
                continue
            path = scratch / f"{entry.task}.{entry.reference.name}"
            try:
                client.get(entry.reference, path, deadline=deadline, progress=progress)
            except ArtifactLost as lost:
                raise ArtifactLost(f"result of task {entry.task}: {lost}", lost.cause) from lost
            current = path
            yield entry, path
    finally:
        if current is not None:
            with contextlib.suppress(OSError):
                current.unlink(missing_ok=True)
