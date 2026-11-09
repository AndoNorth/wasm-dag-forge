"""Usage: python -m wdf_aggregate run --index REF.json --tenant T --aggregation NAME --args JSON
                                   --out DIR --endpoint URL --credentials FILE --role aggregate
python -m wdf_aggregate files --aggregation NAME --args JSON --out DIR [--missing TASK...]
                              TASK=RESULTS...

Exit codes from the aggregate pool of contracts/workload/exit-classes.yaml, 2 on usage. The last
stderr line of a failure is "reason: <class>: <detail>". Prints the peak resident memory.
"""

import argparse
import functools
import json
import resource
import sys
from pathlib import Path

from wdf_artifactclient.client import ArtifactClient, as_reference
from wdf_storecheck.s3 import load_credentials

from .reduce import UsageError
from .run import EXIT_CODES, USAGE, classify, files, run

STUBBED = ()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m wdf_aggregate")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("run", "files"):
        command = commands.add_parser(name)
        command.add_argument("--aggregation", required=True)
        command.add_argument("--args", required=True, type=json.loads, help="a JSON object")
        command.add_argument("--out", required=True, type=Path)
    store = commands.choices["run"]
    store.add_argument("--index", required=True, help="a JSON file holding the index reference")
    store.add_argument("--tenant", required=True)
    store.add_argument("--endpoint", required=True)
    store.add_argument("--credentials", required=True, help="credentials file of the store roles")
    store.add_argument("--role", required=True)
    local = commands.choices["files"]
    local.add_argument("--missing", nargs="*", default=[], metavar="TASK")
    local.add_argument("results", nargs="*", metavar="TASK=RESULTS")
    return root


def peak_rss_mib() -> float:
    """The peak resident memory of this process. Linux reports ru_maxrss in KiB."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def main(argv: list[str]) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as exit:
        return USAGE if exit.code else 0
    try:
        if args.command == "run":
            reference = as_reference(json.loads(Path(args.index).read_text()))
            credentials = load_credentials(args.credentials)[args.role]
            client = ArtifactClient(args.endpoint, credentials)
            call = functools.partial(
                run, client, reference, args.tenant, args.aggregation, args.args, args.out
            )
        else:
            # --missing takes every word after it, and a task id never holds "=", so the
            # results given after it are told apart by their "=".
            missing = [item for item in args.missing if "=" not in item]
            given = [item for item in args.missing if "=" in item] + args.results
            pairs = [item.partition("=") for item in given]
            if any(not task or not sep or not path for task, sep, path in pairs):
                raise UsageError(f"each result is TASK=RESULTS: {given}")
            results = {task: Path(path) for task, _, path in pairs}
            if len(results) != len(pairs):
                raise UsageError("a task is given twice")
            call = functools.partial(files, args.aggregation, args.args, args.out, results, missing)
    except (OSError, ValueError, KeyError) as error:
        print(f"usage: {type(error).__name__}: {error}", file=sys.stderr)
        return USAGE
    try:
        summary = call()
    except UsageError as error:
        print(f"usage: {error}", file=sys.stderr)
        return USAGE
    except Exception as error:
        cls, detail = classify(error)
        print(f"peak_rss_mib: {peak_rss_mib()}")
        print(f"reason: {cls}: {detail}", file=sys.stderr)
        return EXIT_CODES[cls]
    print(f"summary: {summary}")
    print(f"peak_rss_mib: {peak_rss_mib()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
