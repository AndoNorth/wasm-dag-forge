"""`python -m wdf_storecheck run --endpoint URL --credentials FILE --mode base|access`.

An optional `--product NAME` selects the known gaps of that product.
Exit 0 when every check passed or is a recorded known gap.
Exit 1 when a check failed or the store is unreachable, and 2 on usage.
"""

import argparse
import sys
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from .checks import Report, Store, contract_products, contract_roles, run_access, run_base
from .provision import ADMIN
from .s3 import error_code, load_credentials

MODES = {"base": lambda store, report, product: run_base(store, report), "access": run_access}


def run(endpoint, credentials, mode, product=None, say=print) -> int:
    """Runs one mode against a store and returns the exit code."""
    store, report = Store(endpoint, credentials), Report(say)
    try:
        store.admin.list_buckets()
    except (BotoCoreError, ClientError) as error:
        report.line("FAIL", "endpoint", f"{endpoint}: {error_code(error) or type(error).__name__}")
        return 1
    MODES[mode](store, report, product)
    return 1 if report.failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_storecheck")
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run", help="run the contract checks against one endpoint")
    runner.add_argument("--endpoint", required=True)
    runner.add_argument("--credentials", required=True, type=Path)
    runner.add_argument("--mode", required=True, choices=sorted(MODES))
    runner.add_argument(
        "--product",
        choices=list(contract_products()),
        help="the product whose known gaps the contract records, none by default",
    )
    args = parser.parse_args(argv)
    try:
        credentials = load_credentials(args.credentials)
    except ValueError as error:
        parser.error(str(error))
    needed = [ADMIN] if args.mode == "base" else list(contract_roles())
    missing = [r for r in needed if r not in credentials]
    if missing:
        parser.error(f"{args.credentials} has no credential for {', '.join(missing)}")
    return run(args.endpoint, credentials, args.mode, args.product)


if __name__ == "__main__":
    sys.exit(main())
