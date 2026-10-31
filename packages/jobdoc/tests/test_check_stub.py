"""check.sh refuses every invalid schema vector with its code and accepts a valid document."""

import subprocess
import sys

import pytest

CHECK = [sys.executable, "-m", "wdf_jobcheck.check"]


def run(*args):
    return subprocess.run([*CHECK, *args], capture_output=True, text=True)


@pytest.fixture
def vectors(contract):
    return sorted((contract / "vectors/invalid/schema").glob("*/*.json"))


def test_every_schema_vector_is_refused_with_the_code_of_its_directory(vectors):
    for path in vectors:
        result = run(str(path))
        assert result.returncode == 1, path
        assert f"reason: {path.parent.name}:" in result.stderr, path


def test_a_valid_document_exits_0(good, tmp_path):
    import json

    path = tmp_path / "good.json"
    path.write_text(json.dumps(good))
    result = run(str(path))
    assert result.returncode == 0
    assert result.stdout.startswith("ok ")


@pytest.mark.parametrize("args", [[], ["a", "b"], ["/nonexistent.json"]])
def test_usage_errors_exit_2(args):
    result = run(*args)
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_the_shell_entry_point_agrees(contract, vectors):
    script = str(contract / "check.sh")
    refused = subprocess.run([script, str(vectors[0])], capture_output=True, text=True)
    assert refused.returncode == 1
    assert f"reason: {vectors[0].parent.name}:" in refused.stderr
    assert subprocess.run([script], capture_output=True, text=True).returncode == 2
