"""`python -m wdf_migrate apply|status|check --dsn DSN [--allow-contract] [--source DIR]`."""

import argparse
import sys
from pathlib import Path

import psycopg

from . import apply, check, discover, status
from .runner import Refused, from_dirs

OK, REFUSED, UNREACHABLE = 0, 1, 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_migrate")
    parser.add_argument("command", choices=["apply", "status", "check"])
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--allow-contract", action="store_true")
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        help="a directory of migrations, repeatable, replacing the installed packages",
    )
    args = parser.parse_args(argv)
    migrations = from_dirs(args.source) if args.source else discover()
    try:
        if args.command == "apply":
            for name in apply(args.dsn, migrations, allow_contract=args.allow_contract):
                print(f"applied {name}")
        elif args.command == "status":
            for name, state in status(args.dsn, migrations):
                print(f"{state:8} {name}")
        else:
            check(args.dsn, migrations, allow_contract=args.allow_contract)
    except Refused as error:
        print(f"refused: {error}", file=sys.stderr)
        return REFUSED
    except psycopg.OperationalError as error:
        print(f"database unreachable: {error}", file=sys.stderr)
        return UNREACHABLE
    return OK


if __name__ == "__main__":
    sys.exit(main())
