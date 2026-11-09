"""The committed schemas must equal what the models generate."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from wdf_runtime.models import FILES

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = [relative for _, relative in FILES.values()]
# Adds one optional field to a model, then runs the drift check against the committed schemas.
EDIT = """
import sys
from pydantic import create_model
from wdf_runtime import gen, models
model, path = models.FILES[sys.argv[1]]
edited = create_model(model.__name__, __base__=model, hint=(str | None, None))
models.FILES[sys.argv[1]] = (edited, path)
sys.exit(gen.main(["--check"]))
"""


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_runtime.gen", *args], capture_output=True, text=True
    )


def test_committed_files_match_the_models():
    result = run("--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["input", "error", "results", "exit-classes", "build-info"])
def test_a_model_edit_is_reported_with_the_path_of_its_schema(name):
    result = subprocess.run(
        [sys.executable, "-c", EDIT, name], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 1
    assert result.stderr == f"drift: {FILES[name][1]}\n"


@pytest.mark.parametrize("file", SCHEMAS)
def test_a_drifted_file_is_reported_with_its_path(tmp_path, file):
    for name in SCHEMAS:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    path = tmp_path / file
    path.write_text(path.read_text().replace('"type"', '"typo"', 1))
    result = run("--check", "--root", str(tmp_path))
    assert result.returncode == 1
    assert result.stderr == f"drift: {file}\n"


def test_generation_rewrites_a_drifted_file(tmp_path):
    (tmp_path / SCHEMAS[0]).parent.mkdir(parents=True)
    for name in SCHEMAS:
        (tmp_path / name).write_text("{}\n")
    assert run("--root", str(tmp_path)).returncode == 0
    assert run("--check", "--root", str(tmp_path)).returncode == 0
