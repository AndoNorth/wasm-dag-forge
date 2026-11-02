"""The command line of the script, with no store behind it."""

import json
import socket
import subprocess
import sys

import pytest
from wdf_storecheck.checks import Report, classify, contract_roles
from wdf_storecheck.s3 import Creds, dump_credentials, load_credentials


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wdf_storecheck", *args], capture_output=True, text=True
    )


@pytest.fixture
def credentials_file(tmp_path):
    path = tmp_path / "credentials.json"
    dump_credentials(path, {role: Creds(f"{role}-key", "s" * 16) for role in contract_roles()})
    return path


@pytest.mark.parametrize("missing", ["--endpoint", "--credentials", "--mode"])
def test_a_missing_option_is_a_usage_error(missing, credentials_file):
    options = {
        "--endpoint": "http://127.0.0.1:1",
        "--credentials": str(credentials_file),
        "--mode": "base",
    }
    del options[missing]
    result = cli("run", *[part for pair in options.items() for part in pair])
    assert result.returncode == 2
    assert result.stderr.startswith("usage: wdf_storecheck run")


def test_a_mode_of_a_later_issue_is_a_usage_error(credentials_file):
    result = cli(
        "run",
        "--endpoint",
        "http://x",
        "--credentials",
        str(credentials_file),
        "--mode",
        "lifecycle",
    )
    assert result.returncode == 2


def test_a_credentials_file_without_the_admin_role_is_a_usage_error(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"compile": {"access_key": "k", "secret_key": "s"}}))
    result = cli("run", "--endpoint", "http://x", "--credentials", str(path), "--mode", "base")
    assert result.returncode == 2
    assert "admin" in result.stderr


def test_a_closed_port_fails_with_exit_1(credentials_file):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result = cli(
        "run",
        "--endpoint",
        f"http://127.0.0.1:{port}",
        "--credentials",
        str(credentials_file),
        "--mode",
        "base",
    )
    assert result.returncode == 1
    assert result.stdout.startswith("FAIL endpoint")


def test_credentials_round_trip(tmp_path, credentials_file):
    loaded = load_credentials(credentials_file)
    assert sorted(loaded) == sorted(contract_roles())
    path = tmp_path / "other.json"
    dump_credentials(path, loaded)
    assert load_credentials(path) == loaded


@pytest.mark.parametrize("text", ["", "[]", '{"admin": "k"}', '{"admin": {"access_key": "k"}}'])
def test_a_malformed_credentials_file_is_refused(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_credentials(path)


@pytest.mark.parametrize(
    ("listed", "allowed", "state"),
    [(True, True, "known-gap"), (True, False, "FAIL"), (False, True, "FAIL"), (False, False, "ok")],
)
def test_a_lifecycle_edit_is_judged_against_the_recorded_gap(listed, allowed, state):
    assert classify(listed, allowed)[0] == state


def test_a_failed_check_is_counted_and_a_known_gap_is_not():
    lines: list[str] = []
    report = Report(lines.append)
    report.check("good", lambda: None)
    report.line("known-gap", "gap")
    assert report.failures == 0
    report.check("bad", lambda: 1 / 0)
    assert report.failures == 1
    assert lines[-1].startswith("FAIL bad: ZeroDivisionError")
