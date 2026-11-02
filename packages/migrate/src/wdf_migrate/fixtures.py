"""The `appdb` fixture, registered as a pytest plugin by the package entry point."""

import itertools
import time

import psycopg
import pytest

from .runner import apply, discover

PASSWORD = "wdf"
IMAGE = "timescale/timescaledb"
TELEMETRY_OFF = ("-c", "timescaledb.telemetry_level=off")


def _connect(port: int, dbname: str = "postgres", timeout: float = 90) -> psycopg.Connection:
    """The published port accepts connections before the server is up, so retry until it answers."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return psycopg.connect(
                host="127.0.0.1",
                port=port,
                user="postgres",
                password=PASSWORD,
                dbname=dbname,
                autocommit=True,
            )
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="module")
def appdb_server(t1):
    """One pinned TimescaleDB container for a test module, yielding its published port."""
    with t1.container(
        IMAGE, ports={0: 5432}, command=TELEMETRY_OFF, env={"POSTGRES_PASSWORD": PASSWORD}
    ) as container:
        _connect(container.host_ports[0]).close()
        yield container.host_ports[0]


@pytest.fixture
def empty_db(appdb_server):
    """A fresh database with nothing applied, yielding its DSN."""
    names = itertools.count()
    with _connect(appdb_server) as admin:
        name = f"t{time.time_ns()}_{next(names)}"
        admin.execute(f'CREATE DATABASE "{name}"')
    yield f"postgresql://postgres:{PASSWORD}@127.0.0.1:{appdb_server}/{name}"


@pytest.fixture
def appdb(empty_db):
    """A fresh database with every installed migration applied, yielding its DSN."""
    apply(empty_db, discover())
    return empty_db
