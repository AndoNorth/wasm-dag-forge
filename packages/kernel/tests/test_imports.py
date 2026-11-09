"""A guest is a snapshot taken at build time, so a module imported first inside a function body
would be missing when the function runs. Every import of the guest packages is at module top."""

import ast
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
SOURCES = [PACKAGES / "kernel/src/wdf_kernel", PACKAGES / "workloads/src/wdf_workloads"]
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def imports_inside_functions(tree: ast.AST) -> list[int]:
    return sorted(
        {
            node.lineno
            for function in ast.walk(tree)
            if isinstance(function, FUNCTIONS)
            for node in ast.walk(function)
            if isinstance(node, ast.Import | ast.ImportFrom)
        }
    )


def test_no_import_inside_functions():
    found = [
        f"{path.relative_to(PACKAGES)}:{line}"
        for source in SOURCES
        for path in sorted(source.rglob("*.py"))
        for line in imports_inside_functions(ast.parse(path.read_text()))
    ]
    assert all(source.is_dir() for source in SOURCES)
    assert not found


def test_an_import_inside_a_function_is_found():
    tree = ast.parse(
        "import json\n"
        "def f():\n    import os\n"
        "class C:\n    def g(self):\n        if True:\n            from re import X\n"
        "async def h():\n    def i():\n        import sys\n"
    )
    assert imports_inside_functions(tree) == [3, 7, 10]
