"""A built wheel works from a clean virtual environment, away from the repository."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

PROBE = """
import yaml
from wdf_artifactclient.contract import check
from wdf_artifactclient.models import CONTRACT

for name in ("failures.yaml", "staging.yaml"):
    check(yaml.safe_load((CONTRACT / name).read_text()))
for name in ("layout.yaml", "registry.json", "failures.schema.json", "staging.schema.json"):
    assert (CONTRACT / name).is_file(), name
"""


def run(*cmd, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{cmd}: {result.stdout}{result.stderr}"
    return result


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel")
    wheels = work / "wheels"
    run("uv", "build", "--wheel", "--package", "wdf-artifactclient", "-o", str(wheels), cwd=ROOT)
    venv = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(venv))
    python = venv / "bin/python"
    run("uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl")))
    return python


def test_a_wheel_in_a_clean_venv_loads_the_committed_contract(clean_python, tmp_path):
    """Runs isolated from a directory with no repository above it, so a path into the tree fails."""
    probe = subprocess.run(
        [clean_python, "-I", "-c", PROBE], capture_output=True, text=True, cwd=tmp_path
    )
    assert probe.returncode == 0, probe.stderr


def test_a_wheel_loader_checks_a_file_away_from_the_repository(clean_python, tmp_path):
    contract = tmp_path / "failures.yaml"
    contract.write_text((ROOT / "contracts/artifact/failures.yaml").read_text())
    result = subprocess.run(
        [clean_python, "-I", "-m", "wdf_artifactclient.contract", "--check", str(contract)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
