"""The kernel runs inside a guest with no installed packages, so it imports the standard library
and itself only."""

import ast
import sys
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ALLOWED = sys.stdlib_module_names | {"wdf_kernel"}


def imported(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.append(node.module)
    return names


def test_kernel_imports_stdlib_only():
    outside = [
        f"{path.relative_to(PACKAGE)}: {name}"
        for path in sorted((PACKAGE / "src").rglob("*.py"))
        for name in imported(ast.parse(path.read_text()))
        if name.partition(".")[0] not in ALLOWED
    ]
    assert not outside


def test_an_outside_import_is_found_by_the_stdlib_only_check():
    tree = ast.parse("import json\nfrom os import path\nimport yaml\nfrom pydantic import X")
    names = [n for n in imported(tree) if n.partition(".")[0] not in ALLOWED]
    assert names == ["yaml", "pydantic"]


def test_kernel_declares_no_dependency_so_stdlib_only_holds_when_installed():
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == []
