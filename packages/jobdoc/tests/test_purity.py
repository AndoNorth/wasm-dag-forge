"""The validation libraries do no I/O and read no clock, so a verdict follows its inputs."""

import ast
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parents[2]
SOURCES = [PACKAGES / name / "src" for name in ("jobdoc", "jobcheck", "plan", "jobpatch")]

BANNED_IMPORTS = {
    "os",
    "sys",
    "socket",
    "subprocess",
    "pathlib",
    "tempfile",
    "urllib",
    "time",
    "datetime",
    "random",
    "secrets",
    "uuid",
    "logging",
    "wdf_events",
}
BANNED_CALLS = {"open", "print"}
# The modules that exist to touch the file system or the command line.
EXEMPT = {"wdf_jobdoc.profile_files", "wdf_jobdoc.schemagen", "wdf_jobcheck.check"}


def no_io_imports(source: str) -> list[str]:
    """The banned imports and calls in one module's source, as readable findings."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] if node.level == 0 else []
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BANNED_CALLS:
                found.append(f"call {node.func.id}")
            continue
        else:
            continue
        found += [f"import {n}" for n in names if n.split(".")[0] in BANNED_IMPORTS]
    return found


def modules():
    for src in SOURCES:
        for path in sorted(src.rglob("*.py")):
            name = ".".join(path.relative_to(src).with_suffix("").parts).removesuffix(".__init__")
            yield name, path


def test_no_io_imports():
    offenders = {
        name: found
        for name, path in modules()
        if name not in EXEMPT and (found := no_io_imports(path.read_text()))
    }
    assert not offenders


def test_the_exempt_modules_exist():
    assert EXEMPT <= {name for name, _ in modules()}


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import os.path",
        "from pathlib import Path",
        "from datetime import datetime",
        "import wdf_events",
        "from wdf_events import keys",
        "import random",
        "def f():\n    open('x')",
        "def f():\n    print('x')",
    ],
)
def test_the_scanner_flags_what_it_bans(source):
    assert no_io_imports(source)


@pytest.mark.parametrize("source", ["import json", "from decimal import Decimal", "x = 1"])
def test_the_scanner_passes_pure_code(source):
    assert not no_io_imports(source)
