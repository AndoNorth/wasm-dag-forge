import psycopg
import pytest


@pytest.fixture
def db(appdb):
    """A connection to a fresh migrated database, with the scheduled policy jobs switched off.

    The policy jobs fire on their own clock and would cut the fixed times of the test events, so
    a test that wants a policy to act runs the job itself.
    """
    with psycopg.connect(appdb, autocommit=True) as connection:
        connection.execute(
            "SELECT alter_job(job_id, scheduled => false) FROM timescaledb_information.jobs"
            " WHERE hypertable_name = 'event'"
        )
        yield connection
