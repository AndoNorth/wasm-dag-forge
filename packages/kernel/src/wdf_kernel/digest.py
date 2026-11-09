"""FNV-1a 64, and the digest of a result over its parsed values, so equal values give one digest
whatever bytes wrote them."""

from collections.abc import Iterable, Mapping

from .rng import MASK

OFFSET = 0xCBF29CE484222325
PRIME = 0x100000001B3


def fnv1a64(data: bytes, value: int = OFFSET) -> int:
    """The FNV-1a 64 hash of data, continued from value."""
    # A pure Python byte loop runs at about 13 MB/s, so a large result is checked under --max-rows.
    for byte in data:
        value = ((value ^ byte) * PRIME) & MASK
    return value


def _field(tag: bytes, data: bytes) -> bytes:
    return tag + str(len(data)).encode() + b":" + data


def _value(value) -> bytes:
    if isinstance(value, bool):
        raise TypeError(f"{value!r} is not a result value")
    if isinstance(value, int):
        return _field(b"i", str(value).encode())
    if isinstance(value, float):
        return _field(b"f", repr(value).encode())
    if isinstance(value, bytes):
        return _field(b"x", value)
    if isinstance(value, str):
        return _field(b"s", value.encode())
    raise TypeError(f"{value!r} is not a result value")


def digest(seed: int, blocks: Iterable[Mapping]) -> str:
    """The digest of a result: its seed, then each block's id, kind and rows in order, each row's
    columns in name order. Every field is tagged and length prefixed, so no two results collide
    by concatenation. Rows may be any iterable and are read once."""
    value = fnv1a64(_value(seed))
    for block in blocks:
        value = fnv1a64(b"B" + _field(b"s", block["id"].encode()), value)
        value = fnv1a64(_field(b"s", block["kind"].encode()), value)
        for row in block["rows"]:
            data = b"R" + b"".join(
                _field(b"s", name.encode()) + _value(row[name]) for name in sorted(row)
            )
            value = fnv1a64(data, value)
    return f"{value:016x}"
