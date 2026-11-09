"""Usage: python -m wdf_kernel.check results --input input.json --results results.xml [--max-rows N]
python -m wdf_kernel.check summary --summary summary.json --inputs TASK=A.json...
                                   [--missing TASK...]

Exit 0 equal, 1 not equal, 2 usage. Prints "digest: <hex>".
`results` compares the parsed values of a result with the blocks the kernel generates for its
input, never the bytes, and refuses a document with more than --max-rows rows before generating.
"""

import argparse
import json
import sys

from . import expected, summary
from .format import FormatError
from .reader import read, same_blocks

STUBBED = ()

USAGE = 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m wdf_kernel.check")
    commands = root.add_subparsers(dest="command", required=True)
    results = commands.add_parser("results", help="compare a results.xml with its input")
    results.add_argument("--input", required=True, help="the input.json of the simulate task")
    results.add_argument("--results", required=True, help="the results.xml it wrote")
    results.add_argument("--max-rows", type=int, help="refuse a result with more rows than this")
    commands.add_parser("summary", help="compare a summary.json with its inputs", add_help=False)
    return root


def check_results(args: argparse.Namespace) -> int:
    try:
        with open(args.input, "rb") as handle:
            params = json.load(handle)
        workload, seed, workload_args = params["workload"], params["seed"], params["args"]
        source = open(args.results, "rb")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"usage: {type(error).__name__}: {error}", file=sys.stderr)
        return USAGE
    try:
        with source:
            document = read(source)
    except FormatError as error:
        print(f"reason: {error.reason}: {error.detail}", file=sys.stderr)
        return 1
    rows = sum(len(block["rows"]) for block in document["blocks"])
    if args.max_rows is not None and rows > args.max_rows:
        print(
            f"usage: the result has {rows} rows, above --max-rows {args.max_rows}", file=sys.stderr
        )
        return USAGE
    if document["seed"] != seed:
        print(f"differs: the seed is {document['seed']}, expected {seed}", file=sys.stderr)
        return 1
    difference = same_blocks(
        expected.expected_blocks(workload, seed, workload_args), document["blocks"]
    )
    if difference is not None:
        print(f"differs: {difference}", file=sys.stderr)
        return 1
    print(f"digest: {expected.expected_digest(workload, seed, workload_args)}")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["summary"]:
        return summary.main(argv[1:])
    args = parser().parse_args(argv)
    return check_results(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
