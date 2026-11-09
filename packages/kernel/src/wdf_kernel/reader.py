"""Reads a results document in wdf.sim.v1 as a stream of rows that carry their block id.

The document first passes every rule of wdf_kernel.format, which refuses a DOCTYPE before any
entity expands, and only then is it parsed for its values, so the refusal reasons have one
implementation. The source must therefore be seekable, as a file is.
"""

import os
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from typing import BinaryIO

from .format import SCHEMA, FormatError, check_document, parse_value
from .writer import KINDS

Kinds = Mapping[str, Mapping[str, str]]


def _events(source: BinaryIO, kinds: Kinds, max_bytes: int | None) -> Iterator[tuple]:
    """Yields ("results", seed), ("block", id, kind) and ("row", id, kind, values) in file order."""
    size = source.seek(0, os.SEEK_END)
    if max_bytes is not None and size > max_bytes:
        raise FormatError("output-over-cap", f"the document is {size} bytes, above {max_bytes}")
    source.seek(0)
    check_document(source, kinds)
    source.seek(0)
    block = None
    for event, element in ET.iterparse(source, events=("start", "end")):
        if event == "start" and element.tag == "results":
            yield "results", int(element.get("seed"))
        elif event == "start" and element.tag == "block":
            block = element
            yield "block", element.get("id"), element.get("kind")
        elif event == "end" and element.tag == "row":
            block_id, kind = block.get("id"), block.get("kind")
            columns = kinds[kind]
            values = {name: parse_value(columns[name], text) for name, text in element.items()}
            yield "row", block_id, kind, values
            # Drops the row from its block so a long block is held one row at a time.
            block.remove(element)
        elif event == "end" and element.tag == "block":
            block.clear()


def rows(
    source: BinaryIO, kinds: Kinds = KINDS, max_bytes: int | None = None
) -> Iterator[tuple[str, str, dict]]:
    """Yields (block id, kind, values) for each row of the document, the flatten of its blocks.

    Raises FormatError when the document breaks a rule, before any row is yielded, and with
    the reason output-over-cap when it is larger than max_bytes.
    """
    for event in _events(source, kinds, max_bytes):
        if event[0] == "row":
            yield event[1:]


def read(source: BinaryIO, kinds: Kinds = KINDS, max_bytes: int | None = None) -> dict:
    """The whole document as values, shaped as an expected values file: schema, seed, blocks."""
    document = {"schema": SCHEMA, "seed": None, "blocks": []}
    for event in _events(source, kinds, max_bytes):
        if event[0] == "results":
            document["seed"] = event[1]
        elif event[0] == "block":
            document["blocks"].append({"id": event[1], "kind": event[2], "rows": []})
        else:
            document["blocks"][-1]["rows"].append(event[3])
    return document


def plain(value, column_type: str):
    """A value in the one form that equal values share: hex as text, float as its repr."""
    if column_type == "hex" and isinstance(value, bytes):
        return value.hex()
    if column_type == "float":
        return repr(float(value))
    return value


def same_blocks(expected: list, actual: list, kinds: Kinds = KINDS) -> str | None:
    """None when the blocks hold equal values in the same order, else the first difference.

    Hex values compare as text or bytes alike, and a float compares by its repr, so 0.0 and -0.0
    differ as their digests do.
    """

    def normal(blocks):
        return [
            (
                block["id"],
                block["kind"],
                [
                    {k: plain(v, kinds.get(block["kind"], {}).get(k, "")) for k, v in row.items()}
                    for row in block["rows"]
                ],
            )
            for block in blocks
        ]

    want, got = normal(expected), normal(actual)
    if len(want) != len(got):
        return f"{len(got)} blocks, expected {len(want)}"
    for (wid, wkind, wrows), (gid, gkind, grows) in zip(want, got, strict=True):
        if (wid, wkind) != (gid, gkind):
            return f"block {gid} of kind {gkind}, expected block {wid} of kind {wkind}"
        if len(wrows) != len(grows):
            return f"block {gid} has {len(grows)} rows, expected {len(wrows)}"
        for index, (wrow, grow) in enumerate(zip(wrows, grows, strict=True)):
            if wrow != grow:
                return f"block {gid} row {index} is {grow}, expected {wrow}"
    return None
