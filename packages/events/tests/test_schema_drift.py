"""The committed schemas must equal what the models generate."""

import shutil
import subprocess
import sys

from wdf_events.profile import CONTRACT_DIR

ROOT = CONTRACT_DIR.parents[1]
SCHEMA = "contracts/events/schemas/job.accepted/1.0.json"


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_events.schemagen", *args], capture_output=True, text=True
    )


def test_committed_schemas_match_the_models():
    result = run("--check")
    assert result.returncode == 0, result.stderr


def test_a_renamed_field_is_reported_with_the_schema_file(tmp_path):
    shutil.copytree(ROOT / "contracts/events/schemas", tmp_path / "contracts/events/schemas")
    path = tmp_path / SCHEMA
    path.write_text(path.read_text().replace("submission_key", "submission_id"))
    result = run("--check", "--root", str(tmp_path))
    assert result.returncode == 1
    assert SCHEMA in result.stderr


def test_a_schema_without_a_model_is_reported(tmp_path):
    shutil.copytree(ROOT / "contracts/events/schemas", tmp_path / "contracts/events/schemas")
    orphan = tmp_path / "contracts/events/schemas/job.orphan/1.0.json"
    orphan.parent.mkdir()
    orphan.write_text("{}\n")
    result = run("--check", "--root", str(tmp_path))
    assert result.returncode == 1
    assert "job.orphan/1.0.json" in result.stderr


def test_generation_rewrites_a_drifted_file(tmp_path):
    shutil.copytree(ROOT / "contracts/events/schemas", tmp_path / "contracts/events/schemas")
    path = tmp_path / SCHEMA
    path.write_text("{}\n")
    assert run("--root", str(tmp_path)).returncode == 0
    assert run("--check", "--root", str(tmp_path)).returncode == 0
