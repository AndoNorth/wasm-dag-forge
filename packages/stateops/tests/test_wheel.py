"""A built wheel works from a clean virtual environment, away from the repository."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

PACKAGES = (
    "wdf-stateops",
    "wdf-jobstate",
    "wdf-events",
    "wdf-eventstore",
    "wdf-compat",
    "wdf-keys",
    "wdf-migrate",
    "wdf-storecheck",
    "wdf-itest",
)

PROBE = """
import wdf_stateops
from wdf_stateops.invariants import queries
from wdf_stateops.inputs import load
assert load().inputs
from importlib import resources
assert (resources.files('wdf_stateops.migrations') / 'R__roles.sql').is_file()
assert len(queries()) == 6, sorted(queries())
from wdf_stateops.windows import params
assert params()['failure_codes'] and params()['unreferenced_grace']
from wdf_stateops.bench import EVENTS_PER_JOB, job_events, job_row
assert len(job_events(0, job_row(0))) == EVENTS_PER_JOB

"""


def run(*cmd, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{cmd}: {result.stdout}{result.stderr}"
    return result


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel")
    wheels = work / "wheels"
    for package in PACKAGES:
        run("uv", "build", "--wheel", "--package", package, "-o", str(wheels), cwd=ROOT)
    venv = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(venv))
    python = venv / "bin/python"
    run("uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl")))
    return python


def test_a_wheel_in_a_clean_venv_imports_and_lists_its_stubs(clean_python, tmp_path):
    """Runs isolated from a directory with no repository above it, so a path into the tree fails."""
    probe = subprocess.run(
        [clean_python, "-I", "-c", PROBE], capture_output=True, text=True, cwd=tmp_path
    )
    assert probe.returncode == 0, probe.stderr
