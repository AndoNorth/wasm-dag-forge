"""The fault workload: each mode fails the way that produces one class of the simulate pool.

The mode is args["mode"], and CLASSES names the class the runner gives each mode. A mode that
reports its own failure writes error.json with deterministic true.
"""

import json
import math
import os
import sys
import time
from pathlib import Path

from wdf_kernel import rows, writer

STUBBED = ()

MIB = 1024 * 1024
PAGE = 4096
ERROR_FILE = "error.json"
RESULTS = "results.xml"
FAIL_LATE_STEPS = 10


def exit_nonzero(seed: int, args: dict, out: Path) -> None:
    """guest_failed, deterministic: writes error.json and exits nonzero."""
    _fail(out, "fault-exit-nonzero", "the fault workload exits nonzero")


def trap(seed: int, args: dict, out: Path) -> None:
    """guest_failed, not deterministic: an uncaught exception, which the guest reports as a trap,
    with no error file."""
    raise RuntimeError("the fault workload traps")


def memory_cap(seed: int, args: dict, out: Path) -> None:
    """guest_memory_cap: grows the heap one MiB at a time until the memory cap stops it."""
    held = []
    while True:
        held.append(bytearray(MIB))


def memory_kill(seed: int, args: dict, out: Path) -> None:
    """oom_killed: holds args["mib"] MiB, every page touched, which stays below the guest cap and
    is meant to take the container past its limit. A guest that is still alive after that fails
    with guest_failed."""
    held = bytearray(args["mib"] * MIB)
    held[::PAGE] = b"\x01" * len(range(0, len(held), PAGE))
    _fail(out, "fault-memory-kill-survived", f"held {args['mib']} MiB and was not killed")


def hang(seed: int, args: dict, out: Path) -> None:
    """timeout: sleeps without a heartbeat until the runner's deadline stops it."""
    while True:
        time.sleep(1)


def slow_start(seed: int, args: dict, out: Path) -> None:
    """ok: sleeps args["seconds"] before its first and only heartbeat, then writes the results of
    noop."""
    time.sleep(args["seconds"])
    _heartbeat(1, 1)
    with open(out / RESULTS, "wb") as sink:
        writer.write(sink, seed, rows.blocks("noop", seed, {}))


def fail_late(seed: int, args: dict, out: Path) -> None:
    """guest_failed, deterministic: heartbeats up to args["at_progress"] of its steps, then writes
    error.json and exits nonzero."""
    for step in range(1, math.ceil(args["at_progress"] * FAIL_LATE_STEPS) + 1):
        _heartbeat(step, FAIL_LATE_STEPS)
    _fail(out, "fault-fail-late", f"failed at progress {args['at_progress']}")


MODES = {
    "exit_nonzero": (exit_nonzero, "guest_failed"),
    "trap": (trap, "guest_failed"),
    "memory_cap": (memory_cap, "guest_memory_cap"),
    "memory_kill": (memory_kill, "oom_killed"),
    "hang": (hang, "timeout"),
    "slow_start": (slow_start, "ok"),
    "fail_late": (fail_late, "guest_failed"),
}
CLASSES = {mode: cls for mode, (_, cls) in MODES.items()}


def fault(seed: int, args: dict, out: Path) -> None:
    mode = MODES.get(args.get("mode"))
    if mode is None:
        _fail(out, "unknown-fault-mode", f"no fault mode named {args.get('mode')}")
    mode[0](seed, args, out)


def _heartbeat(step: int, steps: int) -> None:
    beat = {"progress": step / steps, "step": step, "steps": steps}
    print(f"HEARTBEAT {json.dumps(beat, separators=(',', ':'))}", file=sys.stderr, flush=True)


def _fail(out: Path, code: str, message: str) -> None:
    error = {"code": code, "deterministic": True, "message": message}
    (out / ERROR_FILE).write_text(json.dumps(error, sort_keys=True, separators=(",", ":")) + "\n")
    os._exit(1)
