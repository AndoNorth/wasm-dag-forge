"""Usage: python -m wdf_runtime.calibrate --component C [--runs 3] [--check CATALOGUE]
                                       [--write CATALOGUE]

Runs cpu_burn in the guest on one uncapped host core and prints "units_per_second: <rate>", the
median of the runs, and "spread: <min>..<max>". With --check, exit 1 when the rate differs from the
catalogue's by more than the catalogue's tolerance. With --write, also measures each level 1
argument set of the kernel's fixtures and writes the catalogue.
"""

import argparse
import collections
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from importlib.resources import as_file, files
from pathlib import Path

import yaml

from .__main__ import DEFAULT_MEMORY_MIB
from .command import OUTPUT, command
from .contract import canonical
from .heartbeat import beats
from .pins import COMPONENTIZE_PY_VERSION, WASMTIME_VERSION, WIT_VERSION
from .run import copy_input, run, warm

STUBBED = ()

CATALOGUE = files(__package__) / "catalogue.yaml"
# The argument sets whose nominal durations the catalogue records.
FIXTURES = files("wdf_kernel") / "fixtures/level1"
# About a second of cpu_burn on the host, timed between its first and last heartbeat, so the
# start of wasmtime and the write of the result are not counted.
UNITS = 600
BEATS = 20
# Far above a run of UNITS at any rate a host gives, so a hung guest still ends the command.
TIMEOUT_S = 300
# A difference from the catalogue's rate above which a measurement fails --check.
TOLERANCE = 0.2
HEADER = """\
# Written by python -m wdf_runtime.calibrate --write, never by hand. Rewritten on every bump of
# wasmtime or componentize-py and on a new host.
# cpu_basis host: every rate and duration is measured on one uncapped host core, and a guest runs
# on one thread. A duration in a pod is the duration here divided by the pod's CPU in cores, so
# four times longer in a 250m pod.
# A --check fails when a measured rate differs from units_per_second by more than tolerance, as a
# fraction of units_per_second.
"""


def rate(component: Path, cache_dir: Path) -> float:
    """Work units per second of one warm cpu_burn run, between its first and last heartbeat."""
    params = {
        "args": {"heartbeat_every": UNITS // BEATS, "units": UNITS},
        "seed": 0,
        "workload": "cpu_burn",
    }
    with tempfile.TemporaryDirectory(prefix="wdf-calibrate-") as scratch:
        scratch = Path(scratch)
        (scratch / "input.json").write_text(canonical(params))
        copy_input(scratch / "input.json", scratch)
        (scratch / OUTPUT).mkdir()
        argv = command(component, scratch, DEFAULT_MEMORY_MIB, TIMEOUT_S, cache_dir)
        timed, other = [], collections.deque(maxlen=5)
        with subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        ) as process:
            for line in process.stderr:
                text = line.decode(errors="replace")
                beat = next(beats([text]), None)
                if beat is None:
                    other.append(text)
                else:
                    timed.append((time.monotonic(), beat["step"]))
    if process.returncode != 0 or len(timed) != BEATS:
        raise RuntimeError(
            f"cpu_burn exited {process.returncode} after {len(timed)} of {BEATS} heartbeats: "
            + "".join(other)
        )
    (first, first_step), (last, last_step) = timed[0], timed[-1]
    return (last_step - first_step) / (last - first)


def duration(component: Path, cache_dir: Path, input_file: Path) -> float:
    """Wall seconds of one warm run of an argument set, from the start of wasmtime to its exit."""
    with tempfile.TemporaryDirectory(prefix="wdf-calibrate-") as scratch:
        started = time.monotonic()
        ran = run(component, input_file, Path(scratch), DEFAULT_MEMORY_MIB, TIMEOUT_S, cache_dir)
        seconds = time.monotonic() - started
    if ran.classified.cls != "ok":
        raise RuntimeError(f"{input_file}: {ran.classified.reason}")
    return seconds


def differs(measured: float, catalogued: float, tolerance: float) -> bool:
    return abs(measured - catalogued) > tolerance * catalogued


def host() -> dict:
    cpu = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        models = [
            line for line in cpuinfo.read_text().splitlines() if line.startswith("model name")
        ]
        if models:
            cpu = models[0].split(":", 1)[1].strip()
    return {"cpu": cpu, "machine": platform.machine(), "system": platform.platform()}


def catalogue(rates: list[float], durations: dict[str, tuple[dict, list[float]]]) -> dict:
    return {
        "cpu_basis": "host",
        "host": host(),
        "versions": {
            "componentize_py": COMPONENTIZE_PY_VERSION,
            "wasmtime": WASMTIME_VERSION,
            "wit": WIT_VERSION,
        },
        "tolerance": TOLERANCE,
        "units_per_second": round(statistics.median(rates), 1),
        "spread": {"runs": len(rates), "min": round(min(rates), 1), "max": round(max(rates), 1)},
        "durations": {
            name: {"params": params, "seconds": round(statistics.median(seconds), 3)}
            for name, (params, seconds) in durations.items()
        },
    }


def load(path=CATALOGUE) -> dict:
    return yaml.safe_load(path.read_text())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m wdf_runtime.calibrate")
    parser.add_argument("--component", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--check", type=Path)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not args.component.is_file():
        parser.error(f"--component {args.component} is not a file")
    if args.check is not None and not args.check.is_file():
        parser.error(f"--check {args.check} is not a file")
    with tempfile.TemporaryDirectory(prefix="wdf-calibrate-cache-") as cache_dir:
        cache_dir = Path(cache_dir)
        warm(args.component, cache_dir)
        rates = [rate(args.component, cache_dir) for _ in range(args.runs)]
        durations = {}
        if args.write is not None:
            for fixture in sorted(FIXTURES.iterdir(), key=lambda d: d.name):
                params = json.loads(fixture.joinpath("input.json").read_text())
                with as_file(fixture / "input.json") as input_file:
                    seconds = [
                        duration(args.component, cache_dir, input_file) for _ in range(args.runs)
                    ]
                durations[fixture.name] = (params, seconds)
    measured = statistics.median(rates)
    print(f"units_per_second: {measured:.1f}")
    print(f"spread: {min(rates):.1f}..{max(rates):.1f} over {len(rates)} runs")
    if args.write is not None:
        written = catalogue(rates, durations)
        args.write.write_text(HEADER + yaml.safe_dump(written, sort_keys=False))
        for name, entry in written["durations"].items():
            print(f"{name}: {entry['seconds']} s")
    if args.check is not None:
        expected = load(args.check)
        if differs(measured, expected["units_per_second"], expected["tolerance"]):
            print(
                f"reason: rate-outside-tolerance: {measured:.1f} units per second against "
                f"{expected['units_per_second']} within {expected['tolerance']:.0%}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
