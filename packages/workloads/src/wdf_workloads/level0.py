"""The level 0 workloads: echo, sum_squares and hash_chain, each writing the blocks the kernel
generates for its (seed, args) to results.xml."""

from functools import partial
from pathlib import Path

from wdf_kernel import rows, writer

RESULTS = "results.xml"


def write(workload: str, seed: int, args: dict, out: Path) -> None:
    """Writes out/results.xml, streaming each row as the kernel makes it."""
    with open(out / RESULTS, "wb") as sink:
        writer.write(sink, seed, rows.blocks(workload, seed, args))


WORKLOADS = {name: partial(write, name) for name in ("echo", "sum_squares", "hash_chain")}
