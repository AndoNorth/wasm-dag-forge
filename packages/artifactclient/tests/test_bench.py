"""The bench against a store that the test provides, and its usage."""

import re

import pytest
from wdf_artifactclient.bench import main
from wdf_storecheck.s3 import dump_credentials


@pytest.fixture(scope="module")
def credentials(store, tmp_path_factory):
    store.store().create_buckets()
    path = tmp_path_factory.mktemp("credentials") / "credentials.json"
    dump_credentials(path, store.credentials)
    return path


@pytest.mark.integration
def test_bench_twice_verifies_every_result_and_builds_the_same_index(store, credentials, capsys):
    argv = ["--results", "60", "--twice", "--endpoint", store.endpoint]
    assert main([*argv, "--credentials", str(credentials)]) == 0
    printed = capsys.readouterr().out
    assert printed.count("results_put: 60\n") == 2
    assert printed.count("results_verified: 60\n") == 2
    assert printed.count("index_entries: 60\n") == 2
    assert printed.count("lists: 0\n") == 2
    hashes = re.findall(r"^index_sha256: ([0-9a-f]{64})$", printed, re.MULTILINE)
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]
    for phase in ("put", "index", "verify"):
        assert printed.count(f"{phase}_seconds: ") == 2


@pytest.mark.integration
def test_bench_exits_one_when_a_count_is_wrong(store, credentials, capsys, monkeypatch):
    from wdf_artifactclient import bench

    real = bench.measure
    monkeypatch.setattr(bench, "measure", lambda *a: {**real(*a), "results_verified": "0"})
    argv = ["--results", "3", "--endpoint", store.endpoint, "--credentials", str(credentials)]
    assert main(argv) == 1
    assert "mismatch: results_verified is 0, not 3" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [[], ["--results", "0"], ["--results", "5", "--endpoint", "http://127.0.0.1:1"]],
    ids=["no-count", "zero-results", "endpoint-without-credentials"],
)
def test_bench_usage_errors_exit_two(argv):
    with pytest.raises(SystemExit) as exited:
        main(argv)
    assert exited.value.code == 2
