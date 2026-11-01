"""The retention loader holds each window to twice the maximum job duration and refuses the rest."""

import subprocess
from pathlib import Path

import pytest
import yaml
from wdf_compat.window import MAX_JOB_DURATION_DAYS

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "contracts/state"
VECTORS = STATE / "vectors/retention"


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([STATE / "check.sh", path], capture_output=True, text=True)


def test_the_committed_windows_are_at_least_twice_the_maximum_job_duration():
    windows = yaml.safe_load((STATE / "retention.yaml").read_text())["windows"]
    days = [days for rule in windows.values() for days in rule.values()]
    assert days and all(d >= 2 * MAX_JOB_DURATION_DAYS for d in days)


def test_the_committed_file_is_valid():
    result = check(STATE / "retention.yaml")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", sorted(VECTORS.glob("valid/*.yaml")), ids=lambda p: p.stem)
def test_a_valid_vector_passes(path):
    result = check(path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "path", sorted(VECTORS.glob("invalid/*/*.yaml")), ids=lambda p: f"{p.parent.name}/{p.stem}"
)
def test_an_invalid_vector_fails_with_the_reason_of_its_directory(path):
    result = check(path)
    assert result.returncode == 1
    assert result.stderr.startswith(f"reason: {path.parent.name}:")


def test_the_three_floor_and_target_reasons_each_have_a_vector():
    names = {p.name for p in (VECTORS / "invalid").iterdir()}
    assert {
        "window-below-twice-max-job-duration",
        "retention-on-job-rows",
        "age-rule-on-content-addressed",
    } <= names


def test_a_missing_file_is_a_usage_error():
    assert check(VECTORS / "nothing.yaml").returncode == 2
