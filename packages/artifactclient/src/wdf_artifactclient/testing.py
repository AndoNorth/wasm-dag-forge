"""Stand-ins for the guest and the worker runner, and the pytest fixtures that run them.

A guest is a Python callable that reads the files of `input/` and writes the files of `output/`.
A runner runs a guest against a scratch directory and returns its combined output and error.
The tests that stage and collect are parametrized by the runner,
so a real runner joins the same tests by adding itself to RUNNERS.
"""

import contextlib
import io
from collections.abc import Callable
from pathlib import Path

import pytest

Guest = Callable[[Path, Path], None]
Runner = Callable[[Guest, Path], bytes]


def make_scratch(root: Path) -> Path:
    """A scratch directory with an `input/` and an `output/`."""
    for name in ("input", "output"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def run_guest(guest: Guest, scratch: Path) -> bytes:
    """Runs the guest against the scratch directory. An exception of the guest propagates."""
    log = io.StringIO()
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        guest(scratch / "input", scratch / "output")
    return log.getvalue().encode()


RUNNERS: dict[str, Runner] = {"stand-in": run_guest}


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    return make_scratch(tmp_path / "scratch")


@pytest.fixture(params=sorted(RUNNERS))
def runner(request: pytest.FixtureRequest) -> Runner:
    return RUNNERS[request.param]
