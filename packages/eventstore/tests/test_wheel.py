"""A built wheel works from a clean virtual environment, away from the repository."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

PROBE = """
import json
from wdf_eventstore.models import CONTRACT, EventRow

for name in ['layout.yaml', 'retention.yaml', 'retention.schema.json', 'event-row/1.json']:
    assert (CONTRACT / name).is_file(), name
schema = json.loads((CONTRACT / 'event-row/1.json').read_text())
assert set(schema["properties"]) == set(EventRow.model_fields)

from importlib import metadata, resources

entry = metadata.entry_points(group='wdf.migrations', name='eventstore')
assert [e.value for e in entry] == ['wdf_eventstore.migrations']
shipped = {f.name for f in resources.files('wdf_eventstore.migrations').iterdir()}
shipped = {n for n in shipped if n.endswith('.sql')}
assert shipped == {'0200_event.sql', 'R__event_policies.sql'}, shipped

"""


def run(*cmd, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{cmd}: {result.stdout}{result.stderr}"
    return result


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel")
    wheels = work / "wheels"
    for package in ("wdf-eventstore", "wdf-events", "wdf-keys", "wdf-compat"):
        run("uv", "build", "--wheel", "--package", package, "-o", str(wheels), cwd=ROOT)
    venv = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(venv))
    python = venv / "bin/python"
    run("uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl")))
    return python


def test_a_wheel_in_a_clean_venv_reads_its_contract_files_and_matches_its_model(
    clean_python, tmp_path
):
    """Runs isolated from a directory with no repository above it, so a path into the tree fails."""
    probe = subprocess.run(
        [clean_python, "-I", "-c", PROBE], capture_output=True, text=True, cwd=tmp_path
    )
    assert probe.returncode == 0, probe.stderr
