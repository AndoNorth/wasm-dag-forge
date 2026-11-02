"""The committed generated files must equal what the model and the layout generate."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from wdf_artifactindex.gen import KINDS_SQL, LAYOUT, SCHEMA

ROOT = Path(__file__).resolve().parents[3]
FILES = [str(SCHEMA), str(LAYOUT), str(KINDS_SQL)]
GENERATED = [str(SCHEMA), str(KINDS_SQL)]


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_artifactindex.gen", *args], capture_output=True, text=True
    )


def scratch(tmp_path):
    for name in FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)


def test_committed_files_match_the_model_and_the_layout():
    result = run("--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("file", GENERATED)
def test_a_drifted_file_is_reported_with_its_path(tmp_path, file):
    scratch(tmp_path)
    path = tmp_path / file
    path.write_text(path.read_text() + "\n")
    result = run("--check", "--root", str(tmp_path))
    assert result.returncode == 1
    assert file in result.stderr


def test_a_kind_added_to_the_layout_is_reported_against_the_kinds_file(tmp_path):
    scratch(tmp_path)
    path = tmp_path / LAYOUT
    layout = yaml.safe_load(path.read_text())
    layout["kinds"]["extra"] = {"bucket": "wdf-sources", "class": "content-addressed", "key": "k"}
    path.write_text(yaml.safe_dump(layout))
    result = run("--check", "--root", str(tmp_path))
    assert result.returncode == 1
    assert "R__artifact_kinds.sql" in result.stderr
    assert str(SCHEMA) not in result.stderr


def test_generation_rewrites_drifted_files(tmp_path):
    scratch(tmp_path)
    for file in GENERATED:
        (tmp_path / file).write_text("{}\n")
    assert run("--root", str(tmp_path)).returncode == 0
    assert run("--check", "--root", str(tmp_path)).returncode == 0
    assert (tmp_path / KINDS_SQL).read_text() == (ROOT / KINDS_SQL).read_text()
