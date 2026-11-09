"""Usage: python -m wdf_kernel.check summary --summary summary.json --inputs TASK=A.json...
                                   [--missing TASK...]

The summary an aggregation must write over its inputs, and the check of a summary.json against it.
The check compares count, min and max exactly and mean and std within a relative 1e-9, since the
aggregate reduces with numpy and no pure Python sum matches a numpy reduction bit for bit.
Exit 0 equal, 1 not equal, 2 usage. Prints "digest: <hex>", the SHA-256 of the summary checked.
"""

import argparse
import hashlib
import json
import math
import statistics
import sys

from .digest import digest
from .rows import blocks
from .writer import KINDS

STUBBED = ()

SCHEMA = "wdf.summary/1"
NUMERIC = ("int", "float")
REL_TOL = 1e-9
USAGE = 2
AGGREGATION_ARGS = {"summary_stats": {"blocks", "columns"}, "digest_list": set()}


def _check_args(aggregation: str, args: dict) -> None:
    if aggregation not in AGGREGATION_ARGS:
        raise ValueError(f"no aggregation {aggregation!r}")
    unknown = set(args) - AGGREGATION_ARGS[aggregation]
    if unknown:
        raise ValueError(f"{aggregation} takes no argument {sorted(unknown)}")
    for name, value in args.items():
        if value is not None and not (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            raise ValueError(f"argument {name} must be a list of strings, not {value!r}")


def _stats(values: list) -> dict:
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "std": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "max": max(values),
    }


def expected_summary(aggregation: str, args: dict, inputs: dict) -> dict:
    """The summary of the inputs, read in ascending task id, the order of an index.

    inputs maps each task id to its input params (workload, seed, args), or to None for an input
    that did not succeed. The rows are those the kernel generates for each input.
    """
    _check_args(aggregation, args)
    tasks = sorted(inputs)
    summary = {
        "schema": SCHEMA,
        "aggregation": aggregation,
        "args": args,
        "inputs": len(tasks),
        "missing": [task for task in tasks if inputs[task] is None],
        "rows": 0,
    }
    present = [(task, inputs[task]) for task in tasks if inputs[task] is not None]
    if aggregation == "digest_list":
        summary["digests"] = []
        for task, params in present:
            seed = params["seed"]
            counted = list(blocks(params["workload"], seed, params["args"]))
            for block in counted:
                block["rows"] = list(block["rows"])
                summary["rows"] += len(block["rows"])
            summary["digests"].append({"task": task, "seed": seed, "digest": digest(seed, counted)})
        return summary
    wanted_blocks, wanted_columns = args.get("blocks"), args.get("columns")
    # ponytail: holds every value of a column, fine for the small argument sets a host checks.
    values: dict[str, dict[str, list]] = {}
    for _, params in present:
        for block in blocks(params["workload"], params["seed"], params["args"]):
            columns = [
                name
                for name, column_type in KINDS[block["kind"]].items()
                if column_type in NUMERIC and (wanted_columns is None or name in wanted_columns)
            ]
            selected = wanted_blocks is None or block["id"] in wanted_blocks
            for row in block["rows"]:
                summary["rows"] += 1
                if selected:
                    for name in columns:
                        values.setdefault(block["id"], {}).setdefault(name, []).append(row[name])
    summary["blocks"] = {
        block_id: {name: _stats(column) for name, column in by_column.items()}
        for block_id, by_column in values.items()
    }
    return summary


def _close(want, got) -> bool:
    if want is None or got is None:
        return want is got
    return isinstance(got, (int, float)) and math.isclose(want, got, rel_tol=REL_TOL, abs_tol=0)


def difference(expected: dict, actual: dict) -> str | None:
    """None when actual equals expected within the tolerance of mean and std, else the first
    difference."""
    if set(expected) != set(actual):
        return f"the keys are {sorted(actual)}, expected {sorted(expected)}"
    for key in sorted(expected):
        if key != "blocks" and expected[key] != actual[key]:
            return f"{key} is {actual[key]!r}, expected {expected[key]!r}"
    want_blocks, got_blocks = expected.get("blocks", {}), actual.get("blocks", {})
    if not isinstance(got_blocks, dict) or set(want_blocks) != set(got_blocks):
        return f"the blocks are {got_blocks!r}, expected the blocks {sorted(want_blocks)}"
    for block_id in sorted(want_blocks):
        want_columns, got_columns = want_blocks[block_id], got_blocks[block_id]
        if not isinstance(got_columns, dict) or set(want_columns) != set(got_columns):
            return f"block {block_id} is {got_columns!r}, expected columns {sorted(want_columns)}"
        for name in sorted(want_columns):
            want, got = want_columns[name], got_columns[name]
            where = f"block {block_id} column {name}"
            if not isinstance(got, dict) or set(want) != set(got):
                return f"{where} is {got!r}, expected {want!r}"
            for stat in ("count", "min", "max"):
                if type(got[stat]) is not type(want[stat]) or got[stat] != want[stat]:
                    return f"{where} {stat} is {got[stat]!r}, expected {want[stat]!r}"
            for stat in ("mean", "std"):
                if not _close(want[stat], got[stat]):
                    return f"{where} {stat} is {got[stat]!r}, expected {want[stat]!r}"
    return None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m wdf_kernel.check summary")
    root.add_argument("--summary", required=True, help="the summary.json an aggregate wrote")
    root.add_argument(
        "--inputs", nargs="+", default=[], metavar="TASK=INPUT", help="each input.json by task id"
    )
    root.add_argument(
        "--missing", nargs="+", default=[], metavar="TASK", help="the inputs that did not succeed"
    )
    return root


def main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        inputs: dict = {}
        for item in args.inputs:
            task, separator, path = item.partition("=")
            if not separator or not task or task in inputs:
                raise ValueError(f"{item!r} is not a new TASK=INPUT")
            with open(path, "rb") as handle:
                inputs[task] = json.load(handle)
        for task in args.missing:
            if task in inputs:
                raise ValueError(f"task {task} is both an input and missing")
            inputs[task] = None
        with open(args.summary, "rb") as handle:
            data = handle.read()
        actual = json.loads(data)
        want = expected_summary(actual["aggregation"], actual["args"], inputs)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"usage: {type(error).__name__}: {error}", file=sys.stderr)
        return USAGE
    found = difference(want, actual)
    if found is not None:
        print(f"differs: {found}", file=sys.stderr)
        return 1
    print(f"digest: {hashlib.sha256(data).hexdigest()}")
    return 0
