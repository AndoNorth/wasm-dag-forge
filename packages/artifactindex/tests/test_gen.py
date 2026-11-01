"""The committed schema must equal what the model generates."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FILES = ["contracts/state/schemas/artifact-row/1.json"]


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_artifactindex.gen", *args], capture_output=True, text=True
    )


def test_committed_files_match_the_model():
    result = run("--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("file", FILES)
def test_a_drifted_file_is_reported_with_its_path(tmp_path, file):
    for name in FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    path = tmp_path / file
    path.write_text(path.read_text().replace('"type"', '"typo"', 1))
    result = run("--check", "--root", str(tmp_path))
    assert result.returncode == 1
    assert file in result.stderr


def test_generation_rewrites_a_drifted_file(tmp_path):
    (tmp_path / FILES[0]).parent.mkdir(parents=True)
    (tmp_path / FILES[0]).write_text("{}\n")
    assert run("--root", str(tmp_path)).returncode == 0
    assert run("--check", "--root", str(tmp_path)).returncode == 0
