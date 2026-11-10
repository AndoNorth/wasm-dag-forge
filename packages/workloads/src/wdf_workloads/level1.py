"""The level 1 workloads: noop, cpu_burn, mem_hold and io_churn, each writing the blocks the kernel
generates for its (seed, args) to results.xml, with a heartbeat on stderr every heartbeat_every
steps."""

import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from wdf_kernel import rows, writer

from .level0 import RESULTS

STUBBED = ()

# The argument that counts the steps of each workload and the block whose rows are those steps.
# noop has neither: it is one step of no work.
STEPS = {
    "cpu_burn": ("units", "series"),
    "mem_hold": ("units", "series"),
    "io_churn": ("rows", "payload"),
}


def heartbeat(step: int, steps: int) -> None:
    beat = json.dumps(
        {"progress": step / steps, "step": step, "steps": steps},
        sort_keys=True,
        separators=(",", ":"),
    )
    print(f"HEARTBEAT {beat}", file=sys.stderr, flush=True)


def heartbeat_every(args: dict) -> int:
    every = args.get("heartbeat_every")
    if isinstance(every, bool) or not isinstance(every, int) or every < 1:
        raise ValueError(
            f"argument heartbeat_every must be an integer of at least 1, not {every!r}"
        )
    return every


def beating(steps: Iterable, total: int, every: int) -> Iterator:
    """Yields each step, and beats once every `every` steps after the step is consumed."""
    for step, item in enumerate(steps, 1):
        yield item
        if step % every == 0:
            heartbeat(step, total)


def blocks(workload: str, seed: int, args: dict) -> list[dict]:
    """The kernel's blocks for (seed, args), with the rows of the step block beating."""
    every = heartbeat_every(args)
    made = rows.blocks(workload, seed, args)
    count, step_block = STEPS[workload]
    for block in made:
        if block["id"] == step_block:
            block["rows"] = beating(block["rows"], args[count], every)
    return made


def write(seed: int, made: list[dict], out: Path) -> None:
    with open(out / RESULTS, "wb") as sink:
        writer.write(sink, seed, made)


def noop(seed: int, args: dict, out: Path) -> None:
    every = heartbeat_every(args)
    write(seed, rows.blocks("noop", seed, args), out)
    if every == 1:
        heartbeat(1, 1)


def cpu_burn(seed: int, args: dict, out: Path) -> None:
    write(seed, blocks("cpu_burn", seed, args), out)


def mem_hold(seed: int, args: dict, out: Path) -> None:
    """Holds mib MiB, every byte written, until the result is written."""
    made = blocks("mem_hold", seed, args)
    held = b"\xa5" * (args["mib"] * rows.MIB)
    write(seed, made, out)
    del held


def io_churn(seed: int, args: dict, out: Path) -> None:
    write(seed, blocks("io_churn", seed, args), out)


WORKLOADS = {"noop": noop, "cpu_burn": cpu_burn, "mem_hold": mem_hold, "io_churn": io_churn}
