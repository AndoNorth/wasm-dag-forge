"""The rules of the result format wdf.sim.v1: how a value is written and what a document holds.

The kinds and their columns come from contracts/workload/results.yaml, which the caller reads and
passes in, so this module stays standard library only and runs inside a guest.
"""

import math
import re
from collections.abc import Mapping
from typing import BinaryIO
from xml.parsers import expat

SCHEMA = "wdf.sim.v1"
COLUMN_TYPES = ("int", "float", "hex", "str")
BLOCK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_INT = re.compile(r"0|-?[1-9][0-9]*")
_HEX = re.compile(r"(?:[0-9a-f]{2})*")
_CHUNK = 1 << 16
# Attribute normalisation turns a raw tab, line feed or carriage return into a space, so all escape.
_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "\t": "&#9;",
    "\n": "&#10;",
    "\r": "&#13;",
}
_ESCAPE = re.compile("[" + re.escape("".join(_ESCAPES)) + "]")

Kinds = Mapping[str, Mapping[str, str]]


class FormatError(ValueError):
    """A broken rule, with the reason code of the workload contract and a detail."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def format_value(column_type: str, value) -> str:
    """The attribute text of a value, before escaping."""
    if column_type == "int" and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if column_type == "float" and isinstance(value, int | float) and not isinstance(value, bool):
        if math.isfinite(value):
            return repr(float(value))
    if column_type == "hex" and isinstance(value, bytes):
        return value.hex()
    if column_type == "str" and isinstance(value, str):
        return value
    raise FormatError("bad-number-format", f"{value!r} is not a {column_type} value")


def parse_value(column_type: str, text: str):
    """The value of an attribute text, refusing any spelling that format_value would not write."""
    if column_type == "int" and _INT.fullmatch(text):
        return int(text)
    if column_type == "float":
        try:
            value = float(text)
        except ValueError:
            value = math.nan
        if math.isfinite(value) and repr(value) == text:
            return value
    if column_type == "hex" and _HEX.fullmatch(text):
        return bytes.fromhex(text)
    if column_type == "str":
        return text
    if column_type not in COLUMN_TYPES:
        raise FormatError("unknown-column-type", f"{column_type} is not one of {COLUMN_TYPES}")
    raise FormatError("bad-number-format", f"{text!r} is not a {column_type} value")


def escape(text: str) -> str:
    """The text of a str value inside a double-quoted attribute."""
    return _ESCAPE.sub(lambda m: _ESCAPES[m.group()], text)


def _attributes(element: str, attrs: dict, names: set[str]) -> None:
    if set(attrs) != names:
        raise FormatError(
            "schema-violation",
            f"{element} has attributes {sorted(attrs)}, expected {sorted(names)}",
        )


def check_document(source: BinaryIO, kinds: Kinds) -> None:
    """Raises FormatError with the first rule of wdf.sim.v1 that the document in source breaks."""
    block_ids: set[str] = set()
    columns: Mapping[str, str] = {}
    depth = 0

    def xml_declaration(version, encoding, standalone):
        if encoding is not None and encoding.lower() != "utf-8":
            raise FormatError("schema-violation", f"the encoding is {encoding}, not UTF-8")

    def doctype(name, system_id, public_id, has_internal_subset):
        raise FormatError("doctype-present", f"the document declares a DOCTYPE {name}")

    def instruction(target, data):
        raise FormatError(
            "schema-violation", f"the document holds the processing instruction {target}"
        )

    def text(data):
        if data.strip():
            raise FormatError(
                "schema-violation", f"text {data.strip()[:40]!r} outside an attribute"
            )

    def start(name, attrs):
        nonlocal columns, depth
        if ":" in name or any(":" in a or a == "xmlns" for a in attrs):
            raise FormatError("schema-violation", f"{name} uses a namespace")
        if depth == 0:
            if name != "results":
                raise FormatError("schema-violation", f"the root is {name}, not results")
            _attributes(name, attrs, {"schema", "seed"})
            if attrs["schema"] != SCHEMA:
                raise FormatError(
                    "schema-violation", f"the schema is {attrs['schema']}, not {SCHEMA}"
                )
            if parse_value("int", attrs["seed"]) < 0:
                raise FormatError("bad-number-format", f"the seed {attrs['seed']} is negative")
        elif depth == 1 and name == "row":
            raise FormatError("row-outside-block", "a row is a direct child of results")
        elif depth == 1 and name == "block":
            _attributes(name, attrs, {"id", "kind"})
            block = attrs["id"]
            if not BLOCK_ID.fullmatch(block):
                raise FormatError("schema-violation", f"the block id {block!r} is not an id")
            if block in block_ids:
                raise FormatError("duplicate-block-id", f"the block id {block} appears twice")
            block_ids.add(block)
            if attrs["kind"] not in kinds:
                raise FormatError("unknown-kind", f"block {block} has the kind {attrs['kind']}")
            columns = kinds[attrs["kind"]]
        elif depth == 2 and name == "row":
            _attributes(name, attrs, set(columns))
            for column, value in attrs.items():
                parse_value(columns[column], value)
        else:
            raise FormatError("schema-violation", f"{name} is not allowed at depth {depth}")
        depth += 1

    def end(name):
        nonlocal depth
        depth -= 1

    parser = expat.ParserCreate()
    parser.XmlDeclHandler = xml_declaration
    parser.StartDoctypeDeclHandler = doctype
    parser.ProcessingInstructionHandler = instruction
    parser.CharacterDataHandler = text
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        while chunk := source.read(_CHUNK):
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError as error:
        raise FormatError(
            "schema-violation", f"the document is not well formed: {error}"
        ) from error
