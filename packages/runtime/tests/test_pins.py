"""Each pin equals the version that the lock file, the installed packages and the nix release
fetches hold, so the copy in wdf_runtime.pins cannot drift."""

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

from wdf_runtime import pins
from wdf_runtime.contract import wit_version

ROOT = Path(__file__).resolve().parents[3]


def locked(name: str) -> dict:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    (package,) = [p for p in lock["package"] if p["name"] == name]
    return package


def test_componentize_py_pin_is_the_locked_version_with_hashes():
    package = locked("componentize-py")
    assert package["version"] == pins.COMPONENTIZE_PY_VERSION
    assert package["wheels"]
    assert all(wheel["hash"].startswith("sha256:") for wheel in package["wheels"])


def test_componentize_py_pin_is_the_installed_version():
    assert version("componentize-py") == pins.COMPONENTIZE_PY_VERSION


def test_compile_package_requires_exactly_the_pinned_componentize_py():
    project = tomllib.loads((ROOT / "packages/compile/pyproject.toml").read_text())["project"]
    assert f"componentize-py=={pins.COMPONENTIZE_PY_VERSION}" in project["dependencies"]


def test_wasmtime_pin_is_the_version_of_the_nix_release_fetch():
    nix = (ROOT / "nix/wasm-tools.nix").read_text()
    fetched = re.findall(r'pname = "wasmtime";\s*version = "([^"]+)";', nix)
    assert fetched == [pins.WASMTIME_VERSION]


def test_wit_pin_is_the_version_of_task_wit():
    assert pins.WIT_VERSION == wit_version() == "1.0.0"
