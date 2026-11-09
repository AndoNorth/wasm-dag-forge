"""The blocks a workload must write for (seed, args), and their digest over parsed values."""

from . import digest, rows

STUBBED = ()


def expected_blocks(workload: str, seed: int, args: dict) -> list[dict]:
    """Each block's id, kind and rows, with every row made, as the reader parses them."""
    return [{**block, "rows": list(block["rows"])} for block in rows.blocks(workload, seed, args)]


def expected_digest(workload: str, seed: int, args: dict) -> str:
    """The digest of the result, streamed over the rows so no large result is held."""
    return digest.digest(seed, rows.blocks(workload, seed, args))
