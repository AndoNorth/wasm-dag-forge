"""The committed schemas must equal what the models generate, and describe the same documents."""

import json
import shutil
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

SCHEMA = "contracts/job/schemas/job/1.0.json"


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_jobdoc.schemagen", *args], capture_output=True, text=True
    )


@pytest.fixture
def tree(tmp_path, root):
    shutil.copytree(root / "contracts/job/schemas", tmp_path / "contracts/job/schemas")
    return tmp_path


def test_committed_schemas_match_the_models():
    result = run("--check")
    assert result.returncode == 0, result.stderr


def test_a_renamed_field_is_reported_with_the_schema_file(tree):
    path = tree / SCHEMA
    path.write_text(path.read_text().replace("memory_mib", "memory_mb"))
    result = run("--check", "--root", str(tree))
    assert result.returncode == 1
    assert SCHEMA in result.stderr


def test_a_missing_schema_is_reported(tree):
    (tree / SCHEMA).unlink()
    result = run("--check", "--root", str(tree))
    assert result.returncode == 1
    assert SCHEMA in result.stderr


def test_a_schema_without_a_model_is_reported(tree):
    orphan = tree / "contracts/job/schemas/job/9.9.json"
    orphan.write_text("{}\n")
    result = run("--check", "--root", str(tree))
    assert result.returncode == 1
    assert "job/9.9.json" in result.stderr


def test_generation_rewrites_a_drifted_file(tree):
    (tree / SCHEMA).write_text("{}\n")
    assert run("--root", str(tree)).returncode == 0
    assert run("--check", "--root", str(tree)).returncode == 0


def test_the_schema_names_its_version(contract):
    schema = json.loads((contract / "schemas/job/1.0.json").read_text())
    assert schema["$id"] == "urn:wdf:schema:job:1.0"
    assert schema["properties"]["schema"]["const"] == "urn:wdf:schema:job:1.0"


def test_a_valid_document_satisfies_the_exported_schema(contract, good):
    schema = json.loads((contract / "schemas/job/1.0.json").read_text())
    Draft202012Validator.check_schema(schema)
    assert not list(Draft202012Validator(schema).iter_errors(good))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d["tasks"]["build"].update(cpu=1),
        lambda d: d["tasks"]["sim"].pop("retries"),
        lambda d: d["tasks"]["sim"]["params"].update(seed={"param": "seed", "x": 1}),
        lambda d: d["tasks"]["agg"]["params"].update(seed=1),
        lambda d: d["tasks"]["sim"].update(type="shell"),
        lambda d: d["tasks"]["agg"]["params"]["args"].update(blocks=[{"param": "x"}]),
        lambda d: d.update(parameters={"n": "float"}),
    ],
)
def test_the_exported_schema_refuses_what_the_loader_refuses(contract, good, mutate):
    schema = json.loads((contract / "schemas/job/1.0.json").read_text())
    mutate(good)
    assert list(Draft202012Validator(schema).iter_errors(good))


@pytest.mark.parametrize("name", ["registry.json", "limits.schema.json"])
def test_the_other_generated_files_are_checked_too(tmp_path, root, name):
    for generated in ("registry.json", "limits.schema.json"):
        (tmp_path / "contracts/job").mkdir(parents=True, exist_ok=True)
        shutil.copy(root / "contracts/job" / generated, tmp_path / "contracts/job" / generated)
    shutil.copytree(root / "contracts/job/schemas", tmp_path / "contracts/job/schemas")
    assert run("--check", "--root", str(tmp_path)).returncode == 0
    (tmp_path / "contracts/job" / name).write_text("{}\n")
    result = run("--check", "--root", str(tmp_path))
    assert result.returncode == 1
    assert f"contracts/job/{name}" in result.stderr


@pytest.mark.parametrize("name", ["plan/1.json", "stats/1.json"])
def test_the_plan_and_stats_schemas_are_valid_and_checked(tree, contract, name):
    Draft202012Validator.check_schema(json.loads((contract / "schemas" / name).read_text()))
    (tree / "contracts/job/schemas" / name).write_text("{}\n")
    result = run("--check", "--root", str(tree))
    assert result.returncode == 1
    assert f"schemas/{name}" in result.stderr
