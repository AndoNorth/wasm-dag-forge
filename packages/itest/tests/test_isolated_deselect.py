"""An isolated test is deselected, never skipped, unless the marker expression asks for it."""

import subprocess
import sys

import pytest
from wdf_itest.fixtures import names_isolated

SAMPLE = """
import pytest

@pytest.mark.integration
@pytest.mark.isolated
def test_sample_isolated():
    pass
"""


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    directory = tmp_path_factory.mktemp("sample")
    (directory / "test_sample.py").write_text(SAMPLE)
    return directory


def run(directory, *marker):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", *marker],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "marker", [(), ("-m", "integration"), ("-m", "not integration"), ("-m", "not isolated")]
)
def test_isolated_deselect_leaves_the_sample_out(sample, marker):
    result = run(sample, *marker)
    assert "1 deselected" in result.stdout, result.stdout
    assert "skipped" not in result.stdout and "passed" not in result.stdout, result.stdout


@pytest.mark.parametrize(
    "expression", ["isolated", "isolated or integration", "integration and isolated"]
)
def test_isolated_deselect_collects_the_sample_when_named(sample, expression):
    result = run(sample, "-m", expression)
    assert result.returncode == 0, result.stdout
    assert "1 passed" in result.stdout and "deselected" not in result.stdout, result.stdout


@pytest.mark.parametrize(
    ("expression", "named"),
    [
        ("", False),
        ("integration", False),
        ("not integration", False),
        ("not isolated", False),
        ("integration and not isolated", False),
        ("isolated", True),
        ("not integration or isolated", True),
        ("(isolated)", True),
    ],
)
def test_isolated_deselect_reads_the_expression(expression, named):
    assert names_isolated(expression) is named
