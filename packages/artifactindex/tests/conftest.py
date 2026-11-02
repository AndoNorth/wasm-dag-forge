"""One connection to a fresh database that has every installed migration applied."""

import psycopg
import pytest


@pytest.fixture
def conn(appdb):
    with psycopg.connect(appdb) as connection:
        yield connection
