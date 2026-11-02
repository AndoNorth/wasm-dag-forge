"""The inputs file and the vectors that its loader accepts and refuses."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "contracts/state/vectors/inputs"
INVALID = sorted((VECTORS / "invalid").glob("*/*.yaml"))


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wdf_stateops.inputs", "--check", str(path)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "path", [ROOT / "contracts/state/inputs.yaml", *VECTORS.glob("valid/*.yaml")]
)
def test_a_valid_file_exits_zero(path):
    assert check(path).returncode == 0


def test_every_reason_has_a_vector():
    assert {p.parent.name for p in INVALID} == {"schema-violation", "unknown-source"}


@pytest.mark.parametrize("path", INVALID, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_an_invalid_file_exits_one_with_its_reason(path):
    result = check(path)
    assert result.returncode == 1
    assert result.stderr.startswith(f"reason: {path.parent.name}: ")


def test_a_missing_file_exits_one():
    assert check(VECTORS / "absent.yaml").returncode == 1
