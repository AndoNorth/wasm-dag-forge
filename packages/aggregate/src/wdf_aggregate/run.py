"""One aggregate over the results named by an aggregate index, read in index order."""

import json
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from wdf_artifactclient.aggregate import open_results, read_index
from wdf_artifactclient.contract import Refused
from wdf_artifactclient.errors import ArtifactLost, StoreUnavailable

from .read import ParseFailed, read_result
from .reduce import (
    Partial,
    UsageError,
    check_args,
    combine,
    digest_summary,
    reduce_stats,
    stats_summary,
    write_summary,
)

STUBBED = ()

# The exit code of each class of the aggregate pool in contracts/workload/exit-classes.yaml.
EXIT_CODES = {
    "aggregate_failed": 1,
    "aggregate_input_unavailable": 3,
    "aggregate_input_corrupt": 4,
    "aggregate_input_refused": 5,
    "aggregate_parse_failed": 6,
}
USAGE = 2


def classify(error: BaseException) -> tuple[str, str]:
    """The class of the aggregate pool that an error of a run falls in, and its detail."""
    if isinstance(error, StoreUnavailable):
        return "aggregate_input_unavailable", error.reason
    if isinstance(error, ArtifactLost):
        return "aggregate_input_corrupt", f"{error.reason} (cause {error.cause.code})"
    if isinstance(error, Refused):
        return "aggregate_input_refused", str(error)
    if isinstance(error, ParseFailed):
        return "aggregate_parse_failed", str(error)
    return "aggregate_failed", f"{type(error).__name__}: {error}"


def heartbeat(step: int, steps: int) -> None:
    beat = json.dumps(
        {"progress": step / steps, "step": step, "steps": steps}, separators=(",", ":")
    )
    print(f"HEARTBEAT {beat}", file=sys.stderr, flush=True)


def aggregate(
    aggregation: str,
    args: dict,
    count: int,
    inputs: Iterable[tuple[str, Path | None]],
    out_dir: Path,
) -> Path:
    """Reduces each input in the order given, which must be ascending task id, into summary.json.

    An input with no file did not succeed and is listed as missing.
    """
    check_args(aggregation, args)
    missing: list[str] = []
    partials: list[Partial] = []
    digests: list[dict] = []
    rows = 0
    for step, (task, path) in enumerate(inputs, start=1):
        if path is None:
            missing.append(task)
        else:
            result = read_result(task, path)
            if aggregation == "summary_stats":
                partials.append(reduce_stats(result.rows(), args))
            else:
                digests.append({"task": task, "seed": result.seed, "digest": result.digest()})
                rows += result.row_count
        heartbeat(step, count)
    if aggregation == "summary_stats":
        summary = stats_summary(args, count, missing, combine(partials))
    else:
        summary = digest_summary(count, missing, rows, digests)
    return write_summary(summary, out_dir)


def run(
    client: Any, index_reference: Any, tenant: str, aggregation: str, args: dict, out_dir: Path
) -> Path:
    """Reads the index, then each result by its own reference, never listing a bucket."""
    check_args(aggregation, args)
    # ponytail: the entries are held in memory so the index is refused before any result is read
    # and the heartbeat knows its step count; stream twice if 100,000-entry indexes press memory.
    entries = list(read_index(client, index_reference, tenant))
    with tempfile.TemporaryDirectory(prefix="wdf-aggregate-") as scratch:
        opened = open_results(client, entries, Path(scratch))
        inputs = ((entry.task, path) for entry, path in opened)
        return aggregate(aggregation, args, len(entries), inputs, out_dir)


def files(
    aggregation: str,
    args: dict,
    out_dir: Path,
    results: dict[str, Path],
    missing: Iterable[str] = (),
) -> Path:
    """The aggregate over local files named by task id, combined in ascending task id."""
    inputs: dict[str, Path | None] = {task: None for task in missing}
    overlap = inputs.keys() & results.keys()
    if overlap:
        raise UsageError(f"tasks both missing and given a file: {sorted(overlap)}")
    inputs |= results
    tasks = sorted(inputs)
    return aggregate(aggregation, args, len(tasks), ((t, inputs[t]) for t in tasks), out_dir)
