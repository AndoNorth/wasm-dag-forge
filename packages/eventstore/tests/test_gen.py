"""The committed schemas and the policy migration equal what the models and retention generate."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RETENTION = "contracts/state/retention.yaml"
POLICIES = "packages/eventstore/src/wdf_eventstore/migrations/R__event_policies.sql"
FILES = [
    "contracts/state/schemas/event-row/1.json",
    "contracts/state/retention.schema.json",
    POLICIES,
]


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_eventstore.gen", *args], capture_output=True, text=True
    )


def scratch(tmp_path) -> Path:
    for name in [RETENTION, *FILES]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def test_committed_files_match_the_model():
    result = run("--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("file", FILES)
def test_a_drifted_file_is_reported_with_its_path(tmp_path, file):
    root = scratch(tmp_path)
    path = root / file
    path.write_text(path.read_text().replace('"type"', '"typo"', 1) + "-- drift\n")
    result = run("--check", "--root", str(root))
    assert result.returncode == 1
    assert file in result.stderr


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("drop_after_days: 14", "drop_after_days: 21"),
        ("compress_after_days: 2", "compress_after_days: 3"),
    ],
)
def test_a_moved_window_makes_the_policy_file_drift(tmp_path, old, new):
    root = scratch(tmp_path)
    retention = root / RETENTION
    assert old in retention.read_text()
    retention.write_text(retention.read_text().replace(old, new))
    result = run("--check", "--root", str(root))
    assert result.returncode == 1
    assert "R__event_policies.sql" in result.stderr


def test_a_window_below_the_floor_is_refused_before_generation(tmp_path):
    root = scratch(tmp_path)
    retention = root / RETENTION
    retention.write_text(
        retention.read_text().replace("drop_after_days: 14", "drop_after_days: 13")
    )
    result = run("--root", str(root))
    assert result.returncode == 1
    assert "window-below-twice-max-job-duration" in result.stderr


def test_generation_rewrites_a_drifted_file(tmp_path):
    root = scratch(tmp_path)
    (root / FILES[0]).write_text("{}\n")
    (root / POLICIES).write_text("")
    assert run("--root", str(root)).returncode == 0
    assert run("--check", "--root", str(root)).returncode == 0
