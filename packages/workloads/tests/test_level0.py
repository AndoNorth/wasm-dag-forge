"""Each level 0 golden vector gives its expected values on the host and in a guest built by the
compile library and run by the runtime library."""

import json
import time
from pathlib import Path

import pytest
from wdf_compile.build import build
from wdf_kernel.reader import read, same_blocks
from wdf_runtime.contract import canonical
from wdf_runtime.contract import check as check_contract
from wdf_runtime.run import run, warm
from wdf_workloads import bundle, level1, main

ROOT = Path(__file__).resolve().parents[3]
LEVEL0 = sorted((ROOT / "contracts/workload/vectors/level0").iterdir())
MEMORY_MIB = 384
GUEST_SECONDS = 1.0


@pytest.fixture(scope="module")
def component(tmp_path_factory) -> tuple[Path, Path]:
    """The workload component and a module cache it is already warm in, so a timed run is warm."""
    work = tmp_path_factory.mktemp("workloads")
    built = build(bundle.write(work), "wdf_workloads.main", work / "built").component
    warm(built, work / "cache")
    return built, work / "cache"


def assert_expected(directory: Path, results: Path) -> None:
    expected = json.loads((directory / "expected.json").read_text())
    check_contract(results)
    with results.open("rb") as source:
        document = read(source)
    assert (document["schema"], document["seed"]) == (expected["schema"], expected["seed"])
    assert same_blocks(expected["blocks"], document["blocks"]) is None


@pytest.mark.parametrize("directory", LEVEL0, ids=lambda d: d.name)
def test_level0_golden_host(directory, tmp_path):
    assert main.execute(directory / "input.json", tmp_path) == 0
    assert_expected(directory, tmp_path / "results.xml")


@pytest.mark.parametrize("directory", LEVEL0, ids=lambda d: d.name)
def test_level0_golden_guest(directory, component, tmp_path):
    started = time.monotonic()
    ran = run(component[0], directory / "input.json", tmp_path, MEMORY_MIB, 30, component[1])
    seconds = time.monotonic() - started
    print(f"{directory.name}: {ran.classified.cls} in {seconds:.3f} s")
    assert ran.classified.cls == "ok", ran.tail.decode(errors="replace")
    assert seconds < GUEST_SECONDS
    assert_expected(directory, tmp_path / "output/results.xml")


def test_unknown_workload_writes_its_error_file(component, tmp_path):
    source = tmp_path / "input.json"
    source.write_text(canonical({"args": {}, "seed": 1, "workload": "nope"}))
    assert main.execute(source, tmp_path) == 1
    check_contract(tmp_path / "error.json")

    ran = run(component[0], source, tmp_path / "guest", MEMORY_MIB, 30, component[1])
    print(ran.classified)
    assert ran.classified == (
        "guest_failed",
        True,
        "guest_failed: unknown-workload: no workload named nope",
    )


def test_every_workload_is_dispatched():
    assert set(main.WORKLOADS) == {
        "echo",
        "sum_squares",
        "hash_chain",
        "noop",
        "cpu_burn",
        "mem_hold",
        "io_churn",
        "fault",
    }
    for name in level1.STUBBED:
        with pytest.raises(NotImplementedError):
            main.WORKLOADS[name](0, {}, Path())
