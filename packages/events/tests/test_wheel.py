"""A built wheel of wdf_events works from a clean virtual environment, away from the repository."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from wdf_events.profile import CONTRACT_DIR

ROOT = CONTRACT_DIR.parents[1]
ACCEPTED = json.loads((CONTRACT_DIR / "vectors/accepted/valid/accepted.json").read_text())
CANONICAL = json.dumps(ACCEPTED, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

# Reads an event on stdin and writes its canonical bytes back, then moves the histogram metric.
PROBE = """
import sys
from wdf_events import from_bytes, to_bytes
from wdf_events.metrics import registry

data = sys.stdin.buffer.read()
assert to_bytes(from_bytes(data)) == data
assert registry.histograms["wdf_event_bytes"].count == 1
"""


def run(*cmd, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{cmd}: {result.stdout}{result.stderr}"
    return result


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel")
    wheels = work / "wheels"
    for package in ("wdf-events", "wdf-keys"):
        run("uv", "build", "--wheel", "--package", package, "-o", str(wheels), cwd=ROOT)
    venv = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(venv))
    python = venv / "bin/python"
    run("uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl")))
    return python


def test_a_wheel_in_a_clean_venv_reads_writes_and_counts(clean_python, tmp_path):
    """Runs isolated from a directory with no repository above it, so a path into the tree fails."""
    probe = subprocess.run(
        [clean_python, "-I", "-c", PROBE],
        input=CANONICAL.encode(),
        capture_output=True,
        cwd=tmp_path,
    )
    assert probe.returncode == 0, probe.stderr.decode()
