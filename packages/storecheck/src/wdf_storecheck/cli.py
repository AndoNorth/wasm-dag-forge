"""`python -m wdf_storecheck run --endpoint URL --credentials FILE --mode MODE`.

The modes are base, access, lifecycle, days-arm, days-check, disk-full, recover,
marker-put, marker-get and capacity.
An optional `--product NAME` selects the known gaps of that product.
`--state FILE` is the file that days-arm writes and days-check reads.
`--exec COMMAND` is the prefix that runs a command inside the store, which recover needs.
`--wait SECONDS` is how long lifecycle waits for the expiry.
Exit 0 when every check passed or is a recorded known gap.
Exit 1 when a check failed or the store is unreachable, and 2 on usage.
"""

import argparse
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from .checks import Report, Store, contract_products, contract_roles, run_access, run_base
from .expiry import run_days_arm, run_days_check, run_lifecycle
from .fullness import (
    run_capacity,
    run_disk_full,
    run_marker_get,
    run_marker_put,
    run_recover,
)
from .provision import ADMIN
from .s3 import error_code, load_credentials

# Past one detection interval of the lifecycle job of the deployed store.
DEFAULT_WAIT = 75 * 60


@dataclass
class Options:
    state: Path = Path("storecheck-days.json")
    exec: list[str] = field(default_factory=list)
    wait: float = DEFAULT_WAIT


MODES = {
    "base": lambda store, report, product, opts: run_base(store, report),
    "access": lambda store, report, product, opts: run_access(store, report, product),
    "lifecycle": lambda store, report, product, opts: run_lifecycle(
        store, report, product, opts.wait
    ),
    "days-arm": lambda store, report, product, opts: run_days_arm(
        store, report, product, opts.state
    ),
    "days-check": lambda store, report, product, opts: run_days_check(
        store, report, product, opts.state
    ),
    "disk-full": lambda store, report, product, opts: run_disk_full(store, report, product),
    "recover": lambda store, report, product, opts: run_recover(store, report, product, opts.exec),
    "marker-put": lambda store, report, product, opts: run_marker_put(store, report, product),
    "marker-get": lambda store, report, product, opts: run_marker_get(store, report, product),
    "capacity": lambda store, report, product, opts: run_capacity(store, report, product),
}


def run(endpoint, credentials, mode, product=None, say=print, options=None) -> int:
    """Runs one mode against a store and returns the exit code."""
    store, report = Store(endpoint, credentials), Report(say)
    try:
        store.admin.list_buckets()
    except (BotoCoreError, ClientError) as error:
        report.line("FAIL", "endpoint", f"{endpoint}: {error_code(error) or type(error).__name__}")
        return 1
    MODES[mode](store, report, product, options or Options())
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
    runner.add_argument("--state", type=Path, default=Options.state)
    runner.add_argument("--exec", default="", help="the command prefix that runs inside the store")
    runner.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    args = parser.parse_args(argv)
    try:
        credentials = load_credentials(args.credentials)
    except ValueError as error:
        parser.error(str(error))
    needed = list(contract_roles()) if args.mode == "access" else [ADMIN]
    missing = [r for r in needed if r not in credentials]
    if missing:
        parser.error(f"{args.credentials} has no credential for {', '.join(missing)}")
    if args.mode == "recover" and not args.exec:
        parser.error("--mode recover needs --exec, the prefix that runs a command in the store")
    options = Options(args.state, shlex.split(args.exec), args.wait)
    return run(args.endpoint, credentials, args.mode, args.product, options=options)


if __name__ == "__main__":
    sys.exit(main())
