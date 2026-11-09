"""The per-file reduce of each aggregation and the summary.json it ends in.

Each result file is reduced on its own into a partial, and the partials are combined in index
order, ascending task id, so the bytes of summary.json depend only on the set of inputs.
"""

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

SCHEMA = "wdf.summary/1"
SUMMARY_NAME = "summary.json"
ARGS = {"summary_stats": {"blocks", "columns"}, "digest_list": set()}

# One parsed row: its block id and its values by column.
Row = tuple[str, dict]


class UsageError(ValueError):
    """An aggregation or its arguments that the registry does not allow."""


def check_args(aggregation: str, args: dict) -> None:
    if aggregation not in ARGS:
        raise UsageError(f"{aggregation!r} is not one of {sorted(ARGS)}")
    if not isinstance(args, dict) or set(args) - ARGS[aggregation]:
        raise UsageError(f"{aggregation} takes the arguments {sorted(ARGS[aggregation])}: {args}")
    for name, value in args.items():
        if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise UsageError(f"{name} is not a list of strings: {value!r}")


@dataclass(frozen=True)
class Stats:
    """Count, mean, the sum of squared deviations, min and max, combinable in a fixed order."""

    count: int
    mean: float
    m2: float
    min: int | float
    max: int | float

    @classmethod
    def of(cls, column: pd.Series) -> "Stats":
        values = column.astype("float64")
        mean = float(values.mean())
        return cls(
            count=len(column),
            mean=mean,
            m2=float(((values - mean) ** 2).sum()),
            min=_plain(column.min()),
            max=_plain(column.max()),
        )

    def __add__(self, other: "Stats") -> "Stats":
        """The parallel combination of Chan, Golub and LeVeque."""
        count = self.count + other.count
        delta = other.mean - self.mean
        return Stats(
            count=count,
            mean=self.mean + delta * other.count / count,
            m2=self.m2 + other.m2 + delta * delta * self.count * other.count / count,
            min=min(self.min, other.min),
            max=max(self.max, other.max),
        )

    def summary(self) -> dict:
        std = math.sqrt(self.m2 / (self.count - 1)) if self.count > 1 else None
        return {
            "count": self.count,
            "mean": self.mean,
            "std": std,
            "min": self.min,
            "max": self.max,
        }


def _plain(value) -> int | float:
    """A numpy scalar as the Python int or float it holds, so an int column stays int."""
    return value.item() if isinstance(value, np.generic) else value


def _numeric(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@dataclass
class Partial:
    """What one result file contributes: its row count and its share of the aggregation."""

    rows: int = 0
    stats: dict[tuple[str, str], Stats] = field(default_factory=dict)


def reduce_stats(rows: Iterable[Row], args: dict) -> Partial:
    """The statistics of every numeric column of the selected blocks of one file."""
    blocks, columns = args.get("blocks"), args.get("columns")
    frames: dict[str, dict[str, list]] = {}
    partial = Partial()
    for block, values in rows:
        partial.rows += 1
        if blocks is not None and block not in blocks:
            continue
        frame = frames.setdefault(block, {})
        for column, value in values.items():
            if _numeric(value) and (columns is None or column in columns):
                frame.setdefault(column, []).append(value)
    for block, data in frames.items():
        frame = pd.DataFrame(data)
        for column in frame.columns:
            partial.stats[(block, column)] = Stats.of(frame[column])
    return partial


def combine(partials: Iterable[Partial]) -> Partial:
    """The partials folded left in the order given, which is index order."""
    total = Partial()
    for partial in partials:
        total.rows += partial.rows
        for key, stats in partial.stats.items():
            total.stats[key] = total.stats[key] + stats if key in total.stats else stats
    return total


def stats_summary(args: dict, inputs: int, missing: list[str], total: Partial) -> dict:
    blocks: dict[str, dict[str, dict]] = {}
    for (block, column), stats in total.stats.items():
        blocks.setdefault(block, {})[column] = stats.summary()
    return {
        "schema": SCHEMA,
        "aggregation": "summary_stats",
        "args": args,
        "inputs": inputs,
        "missing": missing,
        "rows": total.rows,
        "blocks": blocks,
    }


def digest_summary(inputs: int, missing: list[str], rows: int, digests: list[dict]) -> dict:
    return {
        "schema": SCHEMA,
        "aggregation": "digest_list",
        "args": {},
        "inputs": inputs,
        "missing": missing,
        "rows": rows,
        "digests": digests,
    }


def canonical(value) -> str:
    """Sorted keys, no whitespace, floats as their repr and one final line feed."""
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        + "\n"
    )


def write_summary(summary: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SUMMARY_NAME
    path.write_text(canonical(summary))
    return path
