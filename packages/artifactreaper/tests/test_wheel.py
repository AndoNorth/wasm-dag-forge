"""A built wheel works from a clean virtual environment, away from the repository."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

PROBE = """
import wdf_artifactreaper.__main__, wdf_artifactreaper.schedule
import wdf_artifactreaper
from wdf_artifactreaper.reap import load_windows
import importlib.resources as resources
import yaml

contract = resources.files("wdf_artifactreaper") / "contract"
for name in ("layout.yaml", "failures.yaml"):
    assert yaml.safe_load((contract / name).read_text()), name
assert load_windows().checkpoint_keep >= 2
"""


def run(*cmd, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{cmd}: {result.stdout}{result.stderr}"
    return result


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel")
    wheels = work / "wheels"
    for package in (
        "wdf-artifactreaper",
        "wdf-artifactclient",
        "wdf-artifactindex",
        "wdf-compat",
        "wdf-events",
        "wdf-keys",
        "wdf-storecheck",
    ):
        run("uv", "build", "--wheel", "--package", package, "-o", str(wheels), cwd=ROOT)
    venv = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(venv))
    python = venv / "bin/python"
    run("uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl")))
    return python


def test_a_wheel_in_a_clean_venv_loads_its_contract_files_and_imports(clean_python, tmp_path):
    """Runs isolated from a directory with no repository above it, so a path into the tree fails."""
    probe = subprocess.run(
        [clean_python, "-I", "-c", PROBE], capture_output=True, text=True, cwd=tmp_path
    )
    assert probe.returncode == 0, probe.stderr
