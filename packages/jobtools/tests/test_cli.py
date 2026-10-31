"""The command line is a stub: its commands exit 3 and say which lane owns them."""

import subprocess
import sys

import pytest


def run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wdf_jobtools", *args], capture_output=True, text=True
    )


@pytest.mark.parametrize("command", ["validate", "plan", "patch-check"])
def test_commands_are_stubs(command):
    result = run(command)
    assert result.returncode == 3
    assert "not implemented: owned by lane L" in result.stderr


@pytest.mark.parametrize("args", [[], ["nope"]])
def test_unknown_command_is_usage(args):
    assert run(*args).returncode == 2
