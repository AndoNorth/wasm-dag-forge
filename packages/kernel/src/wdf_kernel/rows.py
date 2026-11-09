"""The blocks of each workload as a pure function of (seed, args).

A block is {"id", "kind", "rows"}, and rows is an iterator of {column: parsed value}, made lazily,
so a guest writes the rows while it computes them and a host check never holds a large result.
"""

import hashlib
import json
from collections.abc import Iterator

from .digest import PRIME
from .rng import MASK, SplitMix64

UNIT_ROUNDS = 1000
MIB = 1 << 20


def _count(args: dict, name: str) -> int:
    value = args.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"argument {name} must be an integer of at least 0, not {value!r}")
    return value


def work_unit(rng: SplitMix64, acc: int) -> int:
    """One work unit: UNIT_ROUNDS values of rng folded into acc."""
    for _ in range(UNIT_ROUNDS):
        acc = ((acc ^ rng.next()) * PRIME) & MASK
    return acc


def _echo(seed: int, args: dict) -> list[dict]:
    rows = (
        {
            "key": key,
            "value": json.dumps(
                args[key], sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        }
        for key in sorted(args)
    )
    return [{"id": "args", "kind": "echo", "rows": rows}]


def _sum_squares(seed: int, args: dict) -> list[dict]:
    n = _count(args, "n")
    rows = iter([{"name": "sum_squares", "value": n * (n + 1) * (2 * n + 1) // 6}])
    return [{"id": "result", "kind": "scalar", "rows": rows}]


def _hash_chain_rows(seed: int, rounds: int) -> Iterator[dict]:
    value = seed.to_bytes(8, "big")
    for _ in range(rounds):
        value = hashlib.sha256(value).digest()
    yield {"name": "hash_chain", "value": value}


def _hash_chain(seed: int, args: dict) -> list[dict]:
    return [
        {"id": "result", "kind": "digest", "rows": _hash_chain_rows(seed, _count(args, "rounds"))}
    ]


def _noop(seed: int, args: dict) -> list[dict]:
    return []


def _series_rows(seed: int, units: int) -> Iterator[dict]:
    rng, acc = SplitMix64(seed), 0
    for step in range(units):
        acc = work_unit(rng, acc)
        yield {"step": step, "value": (acc >> 11) * 2.0**-53}


def _cpu_burn(seed: int, args: dict) -> list[dict]:
    return [{"id": "series", "kind": "series", "rows": _series_rows(seed, _count(args, "units"))}]


def _mem_hold(seed: int, args: dict) -> list[dict]:
    mib = _count(args, "mib")
    return [
        {
            "id": "held",
            "kind": "scalar",
            "rows": iter([{"name": "held_bytes", "value": mib * MIB}]),
        },
        {"id": "series", "kind": "series", "rows": _series_rows(seed, _count(args, "units"))},
    ]


def _payload_rows(seed: int, rows: int, row_bytes: int) -> Iterator[dict]:
    rng = SplitMix64(seed)
    for step in range(rows):
        yield {"step": step, "data": rng.bytes(row_bytes)}


def _io_churn(seed: int, args: dict) -> list[dict]:
    rows = _payload_rows(seed, _count(args, "rows"), _count(args, "row_bytes"))
    return [{"id": "payload", "kind": "payload", "rows": rows}]


WORKLOADS = {
    "echo": _echo,
    "sum_squares": _sum_squares,
    "hash_chain": _hash_chain,
    "noop": _noop,
    "cpu_burn": _cpu_burn,
    "mem_hold": _mem_hold,
    "io_churn": _io_churn,
}


def blocks(workload: str, seed: int, args: dict) -> list[dict]:
    """The blocks that workload writes for (seed, args), each with fresh lazy rows."""
    if workload not in WORKLOADS:
        raise ValueError(f"no rows for the workload {workload!r}")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"the seed must be an integer of at least 0, not {seed!r}")
    return WORKLOADS[workload](seed, args)
