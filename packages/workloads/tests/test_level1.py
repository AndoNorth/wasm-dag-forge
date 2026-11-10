"""Each level 1 workload writes the blocks the kernel generates for its (seed, args) and beats once
every heartbeat_every steps, on the host and in a guest built by the compile library and run by
the runtime library."""

import json
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path

import pytest
from wdf_compile.build import build
from wdf_kernel.expected import expected_blocks
from wdf_kernel.reader import read, same_blocks
from wdf_runtime.classify import RESULTS
from wdf_runtime.contract import canonical
from wdf_runtime.contract import check as check_contract
from wdf_runtime.heartbeat import beats
from wdf_runtime.run import run, warm
from wdf_workloads import bundle, level1, main

LEVEL1 = sorted(files("wdf_kernel").joinpath("fixtures/level1").iterdir(), key=lambda d: d.name)
# The simulate slot of the ci preset, whose guest cap is this less GUEST_OVERHEAD_MIB.
MEMORY_MIB = 384


@pytest.fixture(scope="module")
def component(tmp_path_factory) -> tuple[Path, Path]:
    """The workload component and a module cache it is already warm in, so a timed run is warm."""
    work = tmp_path_factory.mktemp("workloads")
    built = build(bundle.write(work), "wdf_workloads.main", work / "built").component
    warm(built, work / "cache")
    return built, work / "cache"


def params(directory) -> dict:
    return json.loads(directory.joinpath("input.json").read_text())


def steps(params: dict) -> int:
    if params["workload"] not in level1.STEPS:
        return 1
    return params["args"][level1.STEPS[params["workload"]][0]]


def assert_expected(params: dict, results: Path) -> None:
    check_contract(results)
    with results.open("rb") as source:
        document = read(source)
    assert document["seed"] == params["seed"]
    expected = expected_blocks(params["workload"], params["seed"], params["args"])
    assert same_blocks(expected, document["blocks"]) is None


def guest(component, input_file: Path, scratch: Path):
    started = time.monotonic()
    ran = run(component[0], input_file, scratch, MEMORY_MIB, 600, component[1])
    print(
        f"{ran.classified.cls}, {ran.heartbeats} heartbeats in {time.monotonic() - started:.3f} s"
    )
    assert ran.classified.cls == "ok", ran.tail.decode(errors="replace")
    return ran


@pytest.mark.parametrize("directory", LEVEL1, ids=lambda d: d.name)
def test_level1_guest_equals_host(directory, component, tmp_path):
    assert main.execute(directory / "input.json", tmp_path) == 0
    assert_expected(params(directory), tmp_path / "results.xml")

    guest(component, directory / "input.json", tmp_path / "guest")
    assert_expected(params(directory), tmp_path / "guest/output/results.xml")


@pytest.mark.parametrize("directory", LEVEL1, ids=lambda d: d.name)
def test_heartbeat_cadence(directory, component, tmp_path, capsys):
    fixture = params(directory)
    every = fixture["args"]["heartbeat_every"]
    expected = steps(fixture) // every

    assert main.execute(directory / "input.json", tmp_path) == 0
    host = list(beats(capsys.readouterr().err.splitlines()))
    assert [beat["step"] for beat in host] == [every * n for n in range(1, expected + 1)]
    assert all(beat["steps"] == steps(fixture) for beat in host)

    ran = guest(component, directory / "input.json", tmp_path / "guest")
    print(f"{directory.name}: {steps(fixture)} steps every {every}, {ran.heartbeats} heartbeats")
    assert ran.heartbeats == expected


@pytest.mark.parametrize("every,count", [(1, 10), (3, 3), (10, 1), (11, 0)])
def test_heartbeat_cadence_when_every_does_not_divide_the_steps(every, count, tmp_path, capsys):
    """A beat follows every heartbeat_every steps, and a remainder short of it never beats."""
    level1.cpu_burn(5, {"heartbeat_every": every, "units": 10}, tmp_path)
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == len(list(beats(lines))) == count
    assert_expected(
        {"args": {"units": 10}, "seed": 5, "workload": "cpu_burn"}, tmp_path / "results.xml"
    )


@pytest.mark.parametrize("every", [0, -1, True, "1", None])
def test_heartbeat_every_must_be_a_positive_integer(every, tmp_path):
    for workload in level1.WORKLOADS.values():
        with pytest.raises(ValueError, match="heartbeat_every"):
            workload(
                1,
                {"heartbeat_every": every, "units": 1, "mib": 0, "rows": 1, "row_bytes": 1},
                tmp_path,
            )


@pytest.mark.slow
def test_io_churn_million_rows_in_guest(component, tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_text(
        canonical(
            {
                "args": {"heartbeat_every": 100_000, "row_bytes": 8, "rows": 1_000_000},
                "seed": 21,
                "workload": "io_churn",
            }
        )
    )
    ran = guest(component, input_file, tmp_path / "guest")
    results = tmp_path / "guest/output/results.xml"
    size = results.stat().st_size
    print(f"results.xml: {size} bytes, cap {RESULTS['cap_bytes']}")
    assert size <= RESULTS["cap_bytes"]
    assert ran.heartbeats == 10

    checked = subprocess.run(
        [sys.executable, "-m", "wdf_kernel.check", "results"]
        + ["--input", str(input_file), "--results", str(results)],
        capture_output=True,
        text=True,
    )
    print(checked.stdout, checked.stderr, sep="")
    assert checked.returncode == 0
