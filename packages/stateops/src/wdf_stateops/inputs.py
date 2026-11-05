"""`python -m wdf_stateops.inputs --dsn DSN [--store-endpoint URL --credentials FILE]`.

Or `--check FILE`, which only validates a file.
Each input of contracts/state/inputs.yaml is printed as `<name> <value>`, an input with one value
per bucket as `<name>{bucket=<bucket>} <value>`, and a labelled query as
`<name>{<label>=<value>} <number>`.
Exit 0 ok, 1 an invalid file or a store that cannot be read, 2 usage and 3 when the database is
unreachable.
"""

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from pathlib import Path

import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from wdf_storecheck.cli import run as run_storecheck
from wdf_storecheck.s3 import load_credentials

from . import windows
from .invariants import check_invariants

CONTRACT = resources.files(__package__) / "contract"
OK, INVALID, UNREACHABLE = 0, 1, 3
KINDS = ("sql", "sql_labelled", "invariants", "store")
STORE_REFS = ("bytes_per_bucket", "capacity_share")
# The data volume of the store container and the shell that reaches it on the local cluster.
STORE_VOLUME = "/data"
STORE_EXEC = "kubectl --context kind-wdf -n wdf-infra exec objectstore-0 -c store --"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description=f"One of {', '.join(KINDS)}.")
    ref: str = Field(
        min_length=1, description="The query of a sql source and the name of any other."
    )
    label: str | None = Field(
        default=None,
        pattern="^[a-z][a-z0-9_]*$",
        description="Of a sql_labelled source only: the name of the first column, which holds the"
        " label, the second holding the number.",
    )


class Threshold(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_above: float


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern="^[a-z][a-z0-9_]*$")
    unit: str = Field(min_length=1)
    source: Source
    threshold: Threshold | None = None


class Inputs(BaseModel):
    """contracts/state/inputs.yaml."""

    model_config = ConfigDict(extra="forbid")

    inputs: list[Input] = Field(min_length=1)


class Refused(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")


class StoreUnavailable(Exception):
    pass


@dataclass
class Store:
    endpoint: str
    credentials: Path
    exec: list[str] = field(default_factory=lambda: shlex.split(STORE_EXEC))


@dataclass
class Collected:
    values: list[tuple[str, int | float]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def parse(text: str) -> Inputs:
    """Raises Refused with the first rule that the file breaks."""
    try:
        inputs = Inputs.model_validate(yaml.safe_load(text))
    except (ValidationError, yaml.YAMLError) as error:
        raise Refused("schema-violation", " ".join(str(error).split())) from error
    names = [i.name for i in inputs.inputs]
    if len(set(names)) != len(names):
        raise Refused("schema-violation", "an input name is used twice")
    for item in inputs.inputs:
        source = item.source
        if source.kind not in KINDS or (source.kind == "store" and source.ref not in STORE_REFS):
            raise Refused("unknown-source", f"{item.name} reads {source.kind} {source.ref!r}")
        if (source.kind == "sql_labelled") != (source.label is not None):
            raise Refused(
                "schema-violation", f"{item.name}: a label column goes with kind sql_labelled only"
            )
    return inputs


def load() -> Inputs:
    return parse((CONTRACT / "inputs.yaml").read_text())


def number(value) -> int | float:
    return float(round(value, 3)) if isinstance(value, Decimal | float) else int(value)


def store_bytes(store: Store) -> dict[str, int]:
    """The bytes per bucket, taken from the capacity mode of the contract script."""
    lines: list[str] = []
    credentials = load_credentials(store.credentials)
    if run_storecheck(store.endpoint, credentials, "capacity", say=lines.append):
        raise StoreUnavailable("; ".join(lines))
    return {bucket: int(n) for _, bucket, n in (line.split() for line in lines)}


def capacity_share(store: Store) -> float:
    """The used share of the data volume in percent, which is the `df` figure inside the pod."""
    df = subprocess.run(
        [*store.exec, "df", "-P", "-k", STORE_VOLUME], capture_output=True, text=True
    )
    if df.returncode:
        raise StoreUnavailable(df.stderr.strip() or f"df exited {df.returncode}")
    _, _, used, available, *_ = df.stdout.splitlines()[-1].split()
    return number(Decimal(used) * 100 / (Decimal(used) + Decimal(available)))


def labelled(conn, item: Input, params: dict) -> list[tuple[str, int | float]]:
    """One value per row of a query whose first column is the label and the second the number."""
    label = item.source.label
    cursor = conn.execute(item.source.ref, params)
    columns = [column.name for column in cursor.description]
    if len(columns) != 2 or columns[0] != label:
        raise Refused(
            "label-column-missing",
            f"{item.name} must return the columns {label} and a number, got {columns}",
        )
    return [(f"{item.name}{{{label}={value}}}", number(n)) for value, n in cursor.fetchall()]


def collect_inputs(conn, inputs: Inputs, store: Store | None = None) -> Collected:
    """Reads every input, and lists the store inputs as skipped when no store is given."""
    collected = Collected()
    params = windows.params()
    for item in inputs.inputs:
        kind, ref = item.source.kind, item.source.ref
        if kind == "store" and store is None:
            collected.skipped.append(item.name)
        elif kind == "sql":
            collected.values.append((item.name, number(conn.execute(ref, params).fetchone()[0])))
        elif kind == "sql_labelled":
            collected.values += labelled(conn, item, params)
        elif kind == "invariants":
            collected.values.append((item.name, len(check_invariants(conn).violations)))
        elif ref == "bytes_per_bucket":
            for bucket, size in store_bytes(store).items():
                collected.values.append((f"{item.name}{{bucket={bucket}}}", size))
        else:
            collected.values.append((item.name, capacity_share(store)))
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wdf_stateops.inputs")
    parser.add_argument("--dsn")
    parser.add_argument("--check", type=Path, metavar="FILE", help="validate a file and stop")
    parser.add_argument("--store-endpoint")
    parser.add_argument("--credentials", type=Path)
    parser.add_argument(
        "--store-exec", default=STORE_EXEC, help="the prefix that runs inside the store"
    )
    args = parser.parse_args(argv)
    if args.check:
        try:
            parse(args.check.read_text())
        except (Refused, OSError) as error:
            print(f"reason: {error}", file=sys.stderr)
            return INVALID
        print(f"ok {args.check}")
        return OK
    if not args.dsn:
        parser.error("--dsn is required unless --check is given")
    if bool(args.store_endpoint) != bool(args.credentials):
        parser.error("--store-endpoint and --credentials go together")
    store = Store(args.store_endpoint, args.credentials, shlex.split(args.store_exec))
    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            collected = collect_inputs(conn, load(), store if args.store_endpoint else None)
    except psycopg.OperationalError as error:
        print(f"database unreachable: {error}", file=sys.stderr)
        return UNREACHABLE
    except (StoreUnavailable, ValueError) as error:
        print(f"store unavailable: {error}", file=sys.stderr)
        return INVALID
    except Refused as error:
        print(f"reason: {error}", file=sys.stderr)
        return INVALID
    for name in collected.skipped:
        print(f"skipped: {name}")
    for name, value in collected.values:
        print(f"{name} {value}")
    return OK


if __name__ == "__main__":
    sys.exit(main())
