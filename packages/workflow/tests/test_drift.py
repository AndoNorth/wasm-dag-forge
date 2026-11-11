"""The committed schemas must equal what the models generate."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from wdf_workflow.models import FILES, schema_path

ROOT = Path(__file__).resolve().parents[3]
SCHEMAS = [schema_path(name) for name in FILES]
# Renames one field of a model, keeping its config, then runs the drift check against the
# committed schemas. An unchanged rebuild is checked first, so the rename is the only difference.
RENAME = """
import sys
from pydantic import create_model
from wdf_workflow import gen, models
name, field = sys.argv[1:]
model, contract, bound = models.FILES[name]


def rebuild(new):
    fields = {
        (new if key == field else key): (info.annotation, info)
        for key, info in model.model_fields.items()
    }
    config = model.model_config
    return create_model(model.__name__, __config__=config, __doc__=model.__doc__, **fields)


models.FILES[name] = (rebuild(field), contract, bound)
assert gen.main(["--check"]) == 0, "a rebuild without the rename already drifts"
models.FILES[name] = (rebuild("renamed"), contract, bound)
assert '"renamed"' in gen.render(name)
sys.exit(gen.main(["--check"]))
"""


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_workflow.gen", *args], capture_output=True, text=True
    )


def test_committed_files_match_the_models():
    result = run("--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "name,field",
    [
        ("task-input", "cache_key"),
        ("task-result", "log_tail"),
        ("failure-details", "reason"),
        ("heartbeat", "started_at"),
        ("status-query", "control_seq"),
        ("control-signal", "reason"),
    ],
)
def test_a_renamed_model_field_is_reported_with_the_path_of_its_schema(name, field):
    result = subprocess.run(
        [sys.executable, "-c", RENAME, name, field], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 1
    assert result.stderr == f"drift: {schema_path(name)}\n"


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
    for name in SCHEMAS:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("{}\n")
    assert run("--root", str(tmp_path)).returncode == 0
    assert run("--check", "--root", str(tmp_path)).returncode == 0
