"""Built wheels work from a clean virtual environment, and the runner finds an installed package."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixture_pkg"

PROBE = """
from wdf_migrate import discover
assert [m.name for m in discover()] == ["0001_fixture_item.sql", "0002_fixture_note.sql"]
"""


def run(*cmd, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{cmd}: {result.stdout}{result.stderr}"
    return result


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel")
    wheels = work / "wheels"
    run("uv", "build", "--wheel", "--package", "wdf-migrate", "-o", str(wheels), cwd=ROOT)
    run("uv", "build", "--wheel", str(FIXTURE), "-o", str(wheels), cwd=work)
    run("uv", "build", "--wheel", "--package", "wdf-itest", "-o", str(wheels), cwd=ROOT)
    venv = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(venv))
    python = venv / "bin/python"
    run("uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl")))
    return python


def test_wheel_finds_the_migrations_of_an_installed_package(clean_python, tmp_path):
    """Runs isolated from a directory with no repository above it, so a path into the tree fails."""
    probe = subprocess.run(
        [clean_python, "-I", "-c", PROBE], capture_output=True, text=True, cwd=tmp_path
    )
    assert probe.returncode == 0, probe.stderr


def test_wheel_cli_reports_an_unreachable_database(clean_python, tmp_path):
    dsn = "postgresql://postgres@127.0.0.1:1/none?connect_timeout=2"
    result = subprocess.run(
        [clean_python, "-I", "-m", "wdf_migrate", "check", "--dsn", dsn],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 3, result.stderr
