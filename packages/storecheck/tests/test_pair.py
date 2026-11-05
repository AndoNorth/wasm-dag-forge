"""A store and a database in one pytest session, as the artifact tests need them."""

import psycopg
import pytest
from wdf_storecheck.s3 import client

pytestmark = [pytest.mark.integration, pytest.mark.pair]


def test_store_and_database_together(store_session, appdb_server):
    assert appdb_server != int(store_session.endpoint.rsplit(":", 1)[1])
    buckets = client(store_session.endpoint, store_session.credentials["admin"]).list_buckets()
    assert "Buckets" in buckets
    with psycopg.connect(
        host="127.0.0.1", port=appdb_server, user="postgres", password="wdf", connect_timeout=10
    ) as conn:
        assert conn.execute("select 1").fetchone() == (1,)
