"""`python -m wdf_stateops.invariants --dsn DSN [--open-workflows FILE]`.

Each query under contracts/state/invariants/ returns the offending rows as a `key` column, so an
empty result passes. Nothing here reads Temporal: the caller lists the open workflow ids.
"""

import argparse
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import psycopg
from wdf_jobstate import status_vocabulary
from wdf_keys import workflow_id

from . import windows

CONTRACT = resources.files(__package__) / "contract" / "invariants"
OK, VIOLATED, UNREACHABLE = 0, 1, 3
NEEDS_WORKFLOWS = "%(open_workflows)s"


@dataclass(frozen=True)
class Report:
    violations: list[tuple[str, str]]
    skipped: list[str]


def queries() -> dict[str, str]:
    """Every invariant by name, the name being the file name without its suffix."""
    files = sorted(CONTRACT.iterdir(), key=lambda f: f.name)
    return {f.name.removesuffix(".sql"): f.read_text() for f in files if f.name.endswith(".sql")}


def check_invariants(conn, open_workflows: list[str] | None = None) -> Report:
    """Runs every invariant, skipping the workflow comparisons when no workflows are given."""
    params = {
        "open_workflows": open_workflows or [],
        "workflow_prefix": workflow_id(""),
        "terminal": sorted(status_vocabulary().terminal),
        **windows.params(),
    }
    violations, skipped = [], []
    for name, sql in queries().items():
        if NEEDS_WORKFLOWS in sql and open_workflows is None:
            skipped.append(name)
            continue
        violations += [(name, key) for (key,) in sorted(conn.execute(sql, params).fetchall())]
    return Report(violations, skipped)


def read_open_workflows(path: Path) -> list[str]:
    """One workflow id per line, blank lines ignored."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_stateops.invariants")
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--open-workflows", type=Path, metavar="FILE")
    args = parser.parse_args(argv)
    open_workflows = read_open_workflows(args.open_workflows) if args.open_workflows else None
    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            report = check_invariants(conn, open_workflows)
    except psycopg.OperationalError as error:
        print(f"database unreachable: {error}", file=sys.stderr)
        return UNREACHABLE
    for name in report.skipped:
        print(f"skipped: {name}")
    for name, key in report.violations:
        print(f"invariant: {name}: {key}")
    return VIOLATED if report.violations else OK


if __name__ == "__main__":
    sys.exit(main())
