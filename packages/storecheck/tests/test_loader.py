"""The loader accepts the committed files and each invalid vector fails with its reason."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
STORE = ROOT / "contracts/store"
COMMITTED = [STORE / "roles.yaml", STORE / "lifecycle.yaml", STORE / "products.yaml"]
VALID = sorted((STORE / "vectors/valid").glob("*.yaml"))
INVALID = sorted((STORE / "vectors/invalid").glob("*/*.yaml"))


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([STORE / "check.sh", path], capture_output=True, text=True)


@pytest.mark.parametrize("path", COMMITTED + VALID, ids=lambda p: p.stem)
def test_a_valid_file_passes(path):
    result = check(path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", INVALID, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_an_invalid_vector_fails_with_the_reason_of_its_directory(path):
    result = check(path)
    assert result.returncode == 1
    assert result.stderr.startswith(f"reason: {path.parent.name}:")


def test_each_reason_has_a_vector():
    reasons = {p.parent.name for p in INVALID}
    assert {
        "unknown-bucket",
        "role-outside-matrix",
        "secret-in-contract",
        "bucket-without-rules",
    } <= reasons


def test_a_missing_file_is_a_usage_error():
    assert check(STORE / "nothing.yaml").returncode == 2
