"""The bench seeding jobs into a database, with the store and the database of one test module."""

import psycopg
import pytest
from wdf_artifactclient.bench import main
from wdf_storecheck.fixtures import running
from wdf_storecheck.s3 import dump_credentials

pytestmark = [pytest.mark.integration, pytest.mark.pair]


@pytest.fixture(scope="module")
def shelf(t1, tmp_path_factory):
    with running(t1, "seaweedfs", tmp_path_factory.mktemp("shelf")) as started:
        started.store().create_buckets()
        yield started


def test_jobs_are_seeded_one_succeeded_and_one_running_with_results_and_a_checkpoint(
    shelf, appdb, tmp_path, capsys
):
    credentials = tmp_path / "credentials.json"
    dump_credentials(credentials, shelf.credentials)
    argv = ["--results", "5", "--endpoint", shelf.endpoint, "--credentials", str(credentials)]
    assert main([*argv, "--dsn", appdb, "--jobs", "2"]) == 0
    printed = dict(line.split(": ") for line in capsys.readouterr().out.splitlines())
    assert printed["results_verified"] == "5"
    with psycopg.connect(appdb, autocommit=True) as conn:
        for status in ("succeeded", "running"):
            job = printed[f"seeded_{status}"]
            assert conn.execute(
                "SELECT status, terminal_at IS NOT NULL FROM accepted_job WHERE job_id = %s", (job,)
            ).fetchone() == (status, status == "succeeded")
            kinds = dict(
                conn.execute(
                    "SELECT kind, count(*) FROM artifact"
                    " WHERE producer_job = %s AND deleted_at IS NULL GROUP BY kind",
                    (job,),
                ).fetchall()
            )
            assert kinds["result"] == 5
            assert kinds["checkpoint"] >= 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--dsn", "postgresql://x"],
        ["--jobs", "2"],
        ["--dsn", "postgresql://x", "--jobs", "1", "--endpoint", "http://x", "--credentials", "c"],
        ["--dsn", "postgresql://x", "--jobs", "2"],
    ],
    ids=["dsn-without-jobs", "jobs-without-dsn", "one-job", "seeding-without-a-store"],
)
def test_seeding_usage_errors_exit_two(argv):
    with pytest.raises(SystemExit) as exited:
        main(["--results", "5", *argv])
    assert exited.value.code == 2
