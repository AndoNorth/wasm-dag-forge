"""The committed generated files must equal what the model and the contracts generate."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VOCAB = "packages/jobstate/src/wdf_jobstate/migrations/R__job_vocab.sql"
STATUS = "contracts/status/status.yaml"
FILES = ["contracts/state/schemas/job-row/1.json", VOCAB]
INPUTS = [STATUS, "contracts/job/limits.yaml"]


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_jobstate.gen", *args], capture_output=True, text=True
    )


def scratch(tmp_path):
    for name in [*FILES, *INPUTS]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def test_committed_files_match_the_model_and_the_contracts():
    result = run("--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("file", FILES)
def test_a_drifted_file_is_reported_with_its_path(tmp_path, file):
    root = scratch(tmp_path)
    path = root / file
    path.write_text(path.read_text() + "-- drift\n")
    result = run("--check", "--root", str(root))
    assert result.returncode == 1
    assert file in result.stderr


def test_an_eleventh_status_in_the_contract_drifts_the_vocabulary_file(tmp_path):
    root = scratch(tmp_path)
    status = root / STATUS
    status.write_text(status.read_text().replace("  - accepted\n", "  - accepted\n  - queued\n", 1))
    result = run("--check", "--root", str(root))
    assert result.returncode == 1
    assert "R__job_vocab.sql" in result.stderr


def test_generation_rewrites_drifted_files(tmp_path):
    root = scratch(tmp_path)
    for name in FILES:
        (root / name).write_text("{}\n")
    assert run("--root", str(root)).returncode == 0
    assert run("--check", "--root", str(root)).returncode == 0
