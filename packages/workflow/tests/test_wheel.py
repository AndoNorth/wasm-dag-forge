"""A built wheel works from a clean virtual environment, away from the repository."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

PROBE = """
import asyncio
from wdf_workflow.contract import exit_classes, failure_classes, naming, pools, registry_pools
from wdf_workflow.models import CONTRACT
from wdf_workflow.placement import PlacementRequest, StaticPlacer
from wdf_workflow.testing import placer_contract
from wdf_workflow.testing.control import FakeControl
from wdf_workflow.testing.pool import staged_outputs

assert naming().namespace == "wdf"
assert set(pools().pools) == registry_pools() == set(staged_outputs())
assert failure_classes()["operator_interrupt"].budget == "free"
assert "guest_memory_cap" in exit_classes()
for name in ("failures.yaml", "status.yaml"):
    assert (CONTRACT / name).read_text(), name
placer_contract(StaticPlacer())
request = PlacementRequest(task_type="compile", cpu_milli=1000, memory_mib=1024, run_timeout_s=600)
assert StaticPlacer().place(request).queue == "wdf-compile"
assert set(FakeControl().activities()) >= {"emit", "load_plan"}
"""


def workspace_closure(package: str) -> list[str]:
    """The workspace packages that `package` needs, itself included, read from uv.lock."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = {p["name"]: p for p in lock["package"]}
    local = {name for name, p in packages.items() if "editable" in p.get("source", {})}
    seen, todo = set(), [package]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += [d["name"] for d in packages[name].get("dependencies", []) if d["name"] in local]
    return sorted(seen)


def run(*cmd, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, f"{cmd}: {result.stdout}{result.stderr}"
    return result


@pytest.fixture(scope="module")
def clean_python(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("wheel")
    wheels = work / "wheels"
    for package in workspace_closure("wdf-workflow"):
        run("uv", "build", "--wheel", "--package", package, "-o", str(wheels), cwd=ROOT)
    venv = work / "venv"
    run("uv", "venv", "--python", sys.executable, str(venv))
    python = venv / "bin/python"
    run("uv", "pip", "install", "--python", str(python), *map(str, wheels.glob("*.whl")))
    return python


def test_wheel_closure_follows_the_lock():
    closure = workspace_closure("wdf-workflow")
    assert {"wdf-workflow", "wdf-artifactreaper", "wdf-temporal", "wdf-storecheck"} <= set(closure)
    assert "wdf-migrate" not in closure


def test_wheel_in_a_clean_venv_loads_both_contracts_from_package_data(clean_python, tmp_path):
    """Runs isolated from a directory with no repository above it, so a path into the tree fails."""
    probe = subprocess.run(
        [clean_python, "-I", "-c", PROBE], capture_output=True, text=True, cwd=tmp_path
    )
    assert probe.returncode == 0, probe.stderr


@pytest.mark.parametrize(
    ("contract", "relative"),
    [
        ("activity", "failure-classes.yaml"),
        ("activity", "vectors/task-input/valid/simulate.json"),
        ("temporal", "naming.yaml"),
        ("temporal", "pools.yaml"),
        ("temporal", "vectors/status-query/valid/running.json"),
    ],
)
def test_wheel_loader_checks_a_file_away_from_the_repository(
    clean_python, tmp_path, contract, relative
):
    name = relative.split("/")[1] if relative.startswith("vectors/") else None
    copy = tmp_path / Path(relative).name
    copy.write_text((ROOT / "contracts" / contract / relative).read_text())
    command = [clean_python, "-I", "-m", "wdf_workflow.contract", "--check", str(copy)]
    result = subprocess.run(
        command + (["--as", name] if name else []), capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
