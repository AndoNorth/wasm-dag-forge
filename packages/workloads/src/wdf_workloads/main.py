"""The entry module of the workload component: the Run export of the command world, dispatching
on the workload named by /in/input.json.

A guest is a snapshot taken at build time, so every workload module is imported here, at the top.
"""

import json
import sys
from pathlib import Path

from . import fault, level0, level1

STUBBED = ()

INPUT = Path("/in/input.json")
OUTPUT = Path("/out")
ERROR_FILE = "error.json"

WORKLOADS = {**level0.WORKLOADS, **level1.WORKLOADS, "fault": fault.fault}


def execute(input_file: Path, out: Path) -> int:
    """Runs the workload that input_file names, writing its files into out, and returns the exit
    code of the guest."""
    params = json.loads(input_file.read_text())
    workload = WORKLOADS.get(params["workload"])
    if workload is None:
        write_error(out, "unknown-workload", f"no workload named {params['workload']}")
        return 1
    workload(params["seed"], params["args"], out)
    return 0


def write_error(out: Path, code: str, message: str) -> None:
    error = {"code": code, "deterministic": True, "message": message}
    (out / ERROR_FILE).write_text(json.dumps(error, sort_keys=True, separators=(",", ":")) + "\n")


class Run:
    def run(self) -> None:
        code = execute(INPUT, OUTPUT)
        if code:
            sys.exit(code)
