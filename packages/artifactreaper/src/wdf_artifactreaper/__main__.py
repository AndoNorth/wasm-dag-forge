"""Usage: python -m wdf_artifactreaper run-once --dsn DSN --endpoint URL --credentials FILE
                                              [--now ISO] [--dry-run]
python -m wdf_artifactreaper schedule ensure|trigger|worker --temporal-address ADDR

Exit 0, 1 a failed pass, 2 usage, 3 database or Temporal unreachable.
`run-once` and `schedule trigger` print one line per counter.
`schedule worker` reads WDF_REAPER_DSN, WDF_REAPER_ENDPOINT and WDF_REAPER_CREDENTIALS.
"""

import argparse
import sys
from datetime import UTC, datetime

import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from temporalio.client import WorkflowFailureError
from wdf_storecheck.s3 import client as s3_client
from wdf_storecheck.s3 import load_credentials

from . import schedule
from .reap import ROLE, ReapFailed, load_windows, reap_once

STUBBED = ()

FAILED, USAGE, UNREACHABLE = 1, 2, 3


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m wdf_artifactreaper")
    commands = root.add_subparsers(dest="command", required=True)
    once = commands.add_parser("run-once", help="run every pass once")
    once.add_argument("--dsn", required=True, help="the database login of the wdf_reaper role")
    once.add_argument("--endpoint", required=True)
    once.add_argument("--credentials", required=True, help="credentials file of the store roles")
    once.add_argument("--now", help="the time to judge ages by, an ISO 8601 time, default now")
    once.add_argument("--dry-run", action="store_true", help="count, and change nothing")
    scheduled = commands.add_parser("schedule", help="the Temporal Schedule of the reaper")
    scheduled.add_argument("action", choices=("ensure", "trigger", "worker"))
    scheduled.add_argument("--temporal-address", required=True)
    return root


def run_once(args: argparse.Namespace) -> int:
    try:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        credentials = load_credentials(args.credentials)[ROLE]
    except (ValueError, KeyError) as error:
        print(f"usage: {type(error).__name__}: {error}", file=sys.stderr)
        return USAGE
    try:
        conn = psycopg.connect(args.dsn, autocommit=True)
    except psycopg.OperationalError as error:
        print(f"database: {error}", file=sys.stderr)
        return UNREACHABLE
    with conn:
        try:
            report = reap_once(
                conn,
                s3_client(args.endpoint, credentials),
                now,
                load_windows(),
                dry_run=args.dry_run,
            )
        except (ReapFailed, ClientError, BotoCoreError, psycopg.Error) as error:
            print(f"failed: {type(error).__name__}: {error}", file=sys.stderr)
            return FAILED
    if not report.ran:
        print("skipped: another run holds the lock", file=sys.stderr)
    for name, value in report.counters().items():
        print(f"{name}: {value}")
    return 0


def run_schedule(args: argparse.Namespace) -> int:
    try:
        result = getattr(schedule, args.action)(args.temporal_address)
    except KeyError as error:
        print(f"usage: missing environment variable {error}", file=sys.stderr)
        return USAGE
    except (RuntimeError, OSError) as error:
        print(f"temporal: {error}", file=sys.stderr)
        return UNREACHABLE
    except (WorkflowFailureError, TimeoutError) as error:
        print(f"failed: {type(error).__name__}: {error}", file=sys.stderr)
        return FAILED
    if args.action == "trigger":
        if result is None:
            print("skipped: the schedule is paused or a pass is already running", file=sys.stderr)
        else:
            for name, value in result.items():
                print(f"{name}: {value}")
    return 0


def main(argv: list[str]) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as stop:
        return int(stop.code or 0)
    if args.command == "run-once":
        return run_once(args)
    return run_schedule(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
