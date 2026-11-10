"""What the pinned wasmtime does with the real workload component: the stderr and exit code of
each failure, the declared minimum memory, a fresh instance per run, the module cache, and the
memory and deadline of a warm and a cold run."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
from wdf_compile import bundle as compile_bundle
from wdf_compile.build import build
from wdf_runtime import pins
from wdf_runtime.classify import Facts, classify, read_error_file
from wdf_runtime.command import GUEST_INPUT, INPUT_FILE, MIB, OUTPUT, PAGE, command
from wdf_runtime.contract import canonical
from wdf_runtime.pins import GUEST_MIN_PAGES, GUEST_OVERHEAD_MIB
from wdf_runtime.run import warm
from wdf_workloads import bundle

ROOT = Path(__file__).resolve().parents[3]
ECHO = json.loads((ROOT / "contracts/workload/vectors/level0/echo/input.json").read_text())
MEMORY_MIB = 384
TIMEOUT_S = 30
# A guest that holds more than a first run's compile takes, so compile memory given back before
# the guest starts would leave no trace in the peak.
HOLD_MIB = 512
HOLD_MEMORY_MIB = 1024
# Below what a first run of the component spends compiling, above what its guest takes.
SHORT_TIMEOUT_S = 0.5
# Wraps the real entry module with a module-level counter, and traps without a Python exception
# for the workload "abort".
PROBE = """\
import json
import os
import sys

from wdf_workloads import main

RUNS = 0


class Run(main.Run):
    def run(self) -> None:
        global RUNS
        RUNS += 1
        print(f"RUNS {RUNS}", file=sys.stderr, flush=True)
        if json.loads(main.INPUT.read_text())["workload"] == "abort":
            os.abort()
        super().run()
"""
# Spawns argv with stdout discarded, waits for it, prints its peak resident KiB and its seconds,
# and exits with its exit code.
WAIT4 = """\
import os
import sys
import time

started = time.monotonic()
discard = [(os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0)]
pid = os.posix_spawnp(sys.argv[1], sys.argv[1:], os.environ, file_actions=discard)
_, status, usage = os.wait4(pid, 0)
print(usage.ru_maxrss, time.monotonic() - started)
sys.exit(os.waitstatus_to_exitcode(status))
"""
UNREACHABLE = "wasm trap: wasm `unreachable` instruction executed"
GUEST_RAN = "failed to invoke `run` function"
PYTHON_RAISED = "Python function threw an unexpected exception"


class Measured(NamedTuple):
    exit_code: int
    stderr: str
    peak_mib: float
    seconds: float


@pytest.fixture(scope="module")
def component(tmp_path_factory) -> Path:
    work = tmp_path_factory.mktemp("workloads")
    return build(bundle.write(work), "wdf_workloads.main", work / "built").component


@pytest.fixture(scope="module")
def warm_cache(component, tmp_path_factory) -> Path:
    """A module cache that the warm-up of the runtime library has filled for the component."""
    cache = tmp_path_factory.mktemp("cache")
    warm(component, cache)
    assert len(entries(cache)) == 1
    return cache


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> Path:
    """The packages of the workload component under an entry module that counts its runs."""
    work = tmp_path_factory.mktemp("probe")
    for name, data in {**bundle.files(), "wdf_pin_probe.py": PROBE.encode()}.items():
        (work / "tree" / name).parent.mkdir(parents=True, exist_ok=True)
        (work / "tree" / name).write_bytes(data)
    tar = compile_bundle.write(work / "tree", work)
    return build(tar, "wdf_pin_probe", work / "built").component


def measured(argv: list[str]) -> Measured:
    """Runs wasmtime as the only child of a small process, which reports its peak resident memory
    and wall time. A child spawned straight from pytest would report pytest's own peak, since exec
    keeps the high-water mark of the memory it replaces."""
    done = subprocess.run(
        [sys.executable, "-c", WAIT4, *argv],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
    )
    peak_kib, seconds = done.stdout.split()
    return Measured(done.returncode, done.stderr, int(peak_kib) / 1024, float(seconds))


def scratch(path: Path, params: dict) -> Path:
    (path / GUEST_INPUT).mkdir(parents=True)
    (path / OUTPUT).mkdir()
    (path / GUEST_INPUT / INPUT_FILE).write_text(canonical(params))
    return path


def fault(mode: str, **args) -> dict:
    return {"args": {"mode": mode, **args}, "seed": 1, "workload": "fault"}


def with_cap(argv: list[str], cap_bytes: int) -> list[str]:
    return [f"max-memory-size={cap_bytes}" if a.startswith("max-memory-size=") else a for a in argv]


def entries(cache: Path) -> set[Path]:
    modules = (cache / "wasmtime/modules").rglob("*")
    return {p for p in modules if p.is_file() and p.suffix != ".stats"}


def in_order(text: str, parts: list[str]) -> bool:
    at = 0
    for part in parts:
        at = text.find(part, at)
        if at < 0:
            return False
        at += len(part)
    return True


def wasmtime_version() -> str:
    return subprocess.run(
        ["wasmtime", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize("pinned", [pins.WASMTIME_VERSION])
def test_pins_name_the_tested_version(pinned):
    """The pin tests ran on the wasmtime that the pins name."""
    version = wasmtime_version()
    print(version)
    assert version.split()[:2] == ["wasmtime", pinned]


# Each failure: the guest, its input, the guest cap and deadline, then wasmtime's exit code, the
# stderr lines it ends with in order, and the class with its determinism.
FAILURES = {
    "timeout": (
        "component",
        fault("hang"),
        1,
        134,
        ["Error: timed out after deadline has elapsed", "Caused by:", "wasm trap: interrupt"],
        ("timeout", False),
    ),
    "memory_cap": (
        "component",
        fault("memory_cap"),
        TIMEOUT_S,
        134,
        ["object type name: MemoryError", PYTHON_RAISED, GUEST_RAN, UNREACHABLE],
        ("guest_memory_cap", True),
    ),
    "uncaught_exception": (
        "component",
        fault("trap"),
        TIMEOUT_S,
        134,
        ["RuntimeError: the fault workload traps", PYTHON_RAISED, GUEST_RAN, UNREACHABLE],
        ("guest_failed", False),
    ),
    "trap": (
        "probe",
        {**ECHO, "workload": "abort"},
        TIMEOUT_S,
        134,
        ["RUNS 1", GUEST_RAN, UNREACHABLE],
        ("guest_failed", False),
    ),
    "nonzero_exit": ("component", fault("exit_nonzero"), TIMEOUT_S, 1, [], ("guest_failed", True)),
}


@pytest.mark.parametrize("case", FAILURES)
def test_pin_failure_stderr_and_exit_code(case, request, warm_cache, tmp_path):
    guest, params, timeout_s, exit_code, lines, expected = FAILURES[case]
    path = scratch(tmp_path / "s", params)
    ran = measured(command(request.getfixturevalue(guest), path, MEMORY_MIB, timeout_s, warm_cache))
    classified = classify(ran.exit_code, ran.stderr, read_error_file(path), Facts())
    print(f"{case}: exit {ran.exit_code} {classified}\n{ran.stderr}")
    assert ran.exit_code == exit_code
    assert in_order(ran.stderr, lines)
    if case == "trap":
        assert PYTHON_RAISED not in ran.stderr
    if case == "nonzero_exit":
        assert ran.stderr == ""
    assert classified[:2] == expected


def test_pin_workload_component_minimum(component, warm_cache, tmp_path):
    """The component declares at most GUEST_MIN_PAGES of initial memory, so a run at that cap
    completes, and a cap one page below what it declares fails at instantiation, which classify
    gives the class of a refused cap. Builds of one bundle differ by a page in what they declare."""

    def capped(name, pages):
        path = scratch(tmp_path / name, ECHO)
        argv = command(component, path, MEMORY_MIB, TIMEOUT_S, warm_cache)
        ran = measured(with_cap(argv, pages * PAGE))
        return ran, classify(ran.exit_code, ran.stderr, read_error_file(path), Facts())

    declared = int(re.search(r"minimum size of (\d+) pages", capped("probe", 1)[0].stderr)[1])
    at, _ = capped("at", GUEST_MIN_PAGES)
    below, classified = capped("below", declared - 1)
    print(f"declares {declared} pages, at {GUEST_MIN_PAGES}: exit {at.exit_code}")
    print(f"{declared - 1} pages: exit {below.exit_code} {classified}\n{below.stderr}")
    assert declared <= GUEST_MIN_PAGES
    assert at.exit_code == 0, at.stderr
    assert below.exit_code == 1
    assert in_order(
        below.stderr,
        [
            "Error: failed to run main module",
            "Caused by:",
            f"memory minimum size of {declared} pages exceeds memory limits",
        ],
    )
    assert GUEST_RAN not in below.stderr
    assert classified[:2] == ("guest_memory_cap", True)


def test_pin_fresh_instance_per_run(probe, warm_cache, tmp_path):
    """State a run leaves in the guest's modules is gone in the next run of the same component."""
    for n in (1, 2):
        ran = measured(command(probe, scratch(tmp_path / f"{n}", ECHO), MEMORY_MIB, 30, warm_cache))
        print(f"run {n}: exit {ran.exit_code} {ran.stderr.strip()}")
        assert (ran.exit_code, ran.stderr) == (0, "RUNS 1\n")
        assert (tmp_path / f"{n}" / OUTPUT / "results.xml").is_file()


def test_pin_module_cache(component, tmp_path):
    """One entry per component under a directory named after the wasmtime build and the binary's
    modification time in ms, shared by every guest cap and deadline but not by a run with none."""
    cache = tmp_path / "cache"

    def ran(memory_mib, timeout_s, drop_timeout=False):
        argv = command(component, scratch(tmp_path / "s", ECHO), memory_mib, timeout_s, cache)
        if drop_timeout:
            at = argv.index(next(a for a in argv if a.startswith("timeout=")))
            argv = argv[: at - 1] + argv[at + 1 :]
        done = measured(argv)
        shutil.rmtree(tmp_path / "s")
        assert done.exit_code == 0, done.stderr
        return entries(cache)

    first = ran(MEMORY_MIB, TIMEOUT_S)
    (entry,) = first
    commit = wasmtime_version().split("(")[1].split()[0]
    binary = Path(shutil.which("wasmtime")).resolve()
    mtime_ms = binary.stat().st_mtime_ns // 1_000_000
    print(f"{entry.parent.name} {entry.name}")
    assert re.fullmatch(rf"wasmtime-{commit}[0-9a-f]*-{mtime_ms}", entry.parent.name)
    assert ran(HOLD_MEMORY_MIB, TIMEOUT_S) == ran(MEMORY_MIB, 2) == ran(MEMORY_MIB, None) == first
    assert len(ran(MEMORY_MIB, TIMEOUT_S, drop_timeout=True) - first) == 1


def test_pin_default_module_cache(component, tmp_path):
    """Without a cache option, wasmtime still caches, under the user's cache directory."""
    xdg = tmp_path / "xdg"
    argv = command(component, scratch(tmp_path / "s", ECHO), MEMORY_MIB, TIMEOUT_S)
    env = {**os.environ, "XDG_CACHE_HOME": str(xdg)}
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, env=env)
    assert done.returncode == 0, done.stderr
    print(sorted(p.relative_to(xdg) for p in entries(xdg)))
    assert len(entries(xdg)) == 1


def test_pin_warm_overhead_within_guest_overhead(component, warm_cache, tmp_path):
    """In a run whose module was compiled by the warm-up, wasmtime holds at most GUEST_OVERHEAD_MIB
    beyond the guest's linear memory, which a guest that does not grow is its declared minimum."""
    filled = entries(warm_cache)
    ran = measured(
        command(component, scratch(tmp_path / "s", ECHO), MEMORY_MIB, TIMEOUT_S, warm_cache)
    )
    overhead = ran.peak_mib - GUEST_MIN_PAGES * PAGE / MIB
    print(f"warm: peak {ran.peak_mib:.1f} MiB, overhead {overhead:.1f} MiB, {ran.seconds:.2f} s")
    assert ran.exit_code == 0, ran.stderr
    assert entries(warm_cache) == filled
    assert overhead <= GUEST_OVERHEAD_MIB


def test_pin_cold_run_keeps_compile_memory(component, tmp_path):
    """A first run does not give its compile memory back before the guest starts, so its guest
    gets only what is left after the compile, and the warm-up must run in its own process."""

    def hold(name, cache):
        argv = command(
            component,
            scratch(tmp_path / name, fault("memory_kill", mib=HOLD_MIB)),
            HOLD_MEMORY_MIB,
            TIMEOUT_S,
            cache,
        )
        return measured(argv)

    compile_only = measured(
        command(component, scratch(tmp_path / "echo", ECHO), MEMORY_MIB, TIMEOUT_S, tmp_path / "a")
    )
    cold, warm_ = hold("cold", tmp_path / "b"), hold("warm", tmp_path / "b")
    print(
        f"cold echo: peak {compile_only.peak_mib:.1f} MiB in {compile_only.seconds:.2f} s\n"
        f"cold hold {HOLD_MIB} MiB: peak {cold.peak_mib:.1f} MiB in {cold.seconds:.2f} s\n"
        f"warm hold {HOLD_MIB} MiB: peak {warm_.peak_mib:.1f} MiB in {warm_.seconds:.2f} s"
    )
    assert compile_only.exit_code == 0, compile_only.stderr
    # memory_kill fails on its own once it has held its memory, when no container stops it.
    assert cold.exit_code == warm_.exit_code == 1
    assert "fault-memory-kill-survived" in (tmp_path / "cold" / OUTPUT / "error.json").read_text()
    assert compile_only.peak_mib - GUEST_MIN_PAGES * PAGE / MIB > GUEST_OVERHEAD_MIB
    assert warm_.peak_mib > compile_only.peak_mib, "the guest must hold more than the compile"
    assert cold.peak_mib - warm_.peak_mib > GUEST_OVERHEAD_MIB


def test_pin_timeout_against_compile_time(component, tmp_path):
    """-W timeout counts only the guest's own run: a first run whose compile takes longer than the
    deadline still completes, so a deadline never needs room for the compile."""
    ran = measured(
        command(
            component, scratch(tmp_path / "s", ECHO), MEMORY_MIB, SHORT_TIMEOUT_S, tmp_path / "c"
        )
    )
    print(f"cold, -W timeout {SHORT_TIMEOUT_S} s: exit {ran.exit_code} in {ran.seconds:.2f} s")
    assert ran.exit_code == 0, ran.stderr
    assert ran.seconds > SHORT_TIMEOUT_S
