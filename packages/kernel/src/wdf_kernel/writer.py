"""Writes a results document in wdf.sim.v1 as it goes, one row at a time.

A guest imports this module while it is built, so every import is at the top of the module.
"""

from collections.abc import Iterable, Mapping
from typing import BinaryIO

from .format import BLOCK_ID, SCHEMA, FormatError, escape, format_value

# The kinds of contracts/workload/results.yaml, which the kernel cannot parse as it reads no YAML.
# test_writer holds this copy equal to the contract file.
KINDS: dict[str, dict[str, str]] = {
    "echo": {"key": "str", "value": "str"},
    "scalar": {"name": "str", "value": "int"},
    "digest": {"name": "str", "value": "hex"},
    "series": {"step": "int", "value": "float"},
    "payload": {"step": "int", "data": "hex"},
}

DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'


def write(
    sink: BinaryIO,
    seed: int,
    blocks: Iterable[Mapping],
    kinds: Mapping[str, Mapping[str, str]] = KINDS,
) -> None:
    """Writes the document of seed and blocks to sink, refusing any value the reader would refuse.

    Each block is a mapping of `id`, `kind` and `rows`, each row a mapping of every column of the
    kind to its value: int, float, bytes for hex, str. Blocks and rows may be generators, so a
    result of any size is written without holding it.
    """
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise FormatError("bad-number-format", f"the seed {seed!r} is not a non-negative int")
    sink.write(DECLARATION)
    sink.write(f'<results schema="{SCHEMA}" seed="{seed}">\n'.encode())
    seen: set[str] = set()
    for block in blocks:
        block_id, kind = block["id"], block["kind"]
        if not isinstance(block_id, str) or not BLOCK_ID.fullmatch(block_id):
            raise FormatError("schema-violation", f"the block id {block_id!r} is not an id")
        if block_id in seen:
            raise FormatError("duplicate-block-id", f"the block id {block_id} appears twice")
        seen.add(block_id)
        if kind not in kinds:
            raise FormatError("unknown-kind", f"block {block_id} has the kind {kind}")
        columns = kinds[kind]
        sink.write(f'  <block id="{block_id}" kind="{kind}">\n'.encode())
        for row in block["rows"]:
            if set(row) != set(columns):
                raise FormatError(
                    "schema-violation", f"a row of {block_id} has the columns {sorted(row)}"
                )
            attributes = " ".join(
                f'{name}="{escape(format_value(column_type, row[name]))}"'
                for name, column_type in columns.items()
            )
            sink.write(f"    <row {attributes}/>\n".encode())
        sink.write(b"  </block>\n")
    sink.write(b"</results>\n")
