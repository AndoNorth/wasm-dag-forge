"""The class of a finished guest run, from the simulate pool of the exit class table."""

import json
import re
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from .command import OUTPUT, STAGING
from .models import ERROR_FILE_MAX_BYTES, Error

STUBBED = ()

RESULTS = STAGING["task_types"]["simulate"]["outputs"][0]
ERROR_FILE = "error.json"
DETAIL_CHARS = 200
# What wasmtime 48 writes to stderr, as the last lines of its error report.
INTERRUPTED = re.compile(r"wasm trap: interrupt$", re.MULTILINE)
OUT_OF_MEMORY = re.compile(
    r"forcing trap when growing memory|exceeds memory limits$"
    r"|^(?:object type name: )?MemoryError\b",
    re.MULTILINE,
)
# wasmtime's own report starts with an "Error: " line, and names the run function only when the
# guest got as far as running.
REPORT = re.compile(r"^Error: failed to ", re.MULTILINE)
GUEST_RAN = "failed to invoke `run` function"
# wasmtime exits 1 when the guest exits nonzero or wasmtime fails, and 134 when the guest traps.
GUEST_EXITS = (1, 134)


class Classified(NamedTuple):
    cls: str
    deterministic: bool
    reason: str


class Facts(NamedTuple):
    """What the runner knows beyond wasmtime's exit and stderr."""

    cap_refused: str | None = None
    host_error: str | None = None
    oom_event: bool = False
    input_modified: bool = False
    results_bytes: int | None = None


def classify(
    exit_code: int | None,
    stderr_tail: str,
    error_file: dict | None,
    runner_facts: Facts,
) -> Classified:
    """exit_code is None when wasmtime never ran, and negative when a signal stopped it."""
    facts = runner_facts
    if facts.cap_refused is not None:
        return Classified("guest_memory_cap", True, f"guest_memory_cap: {facts.cap_refused}")
    if facts.host_error is not None:
        return Classified("host_error", False, f"host_error: {facts.host_error}")
    if facts.oom_event:
        return Classified(
            "oom_killed", False, f"oom_killed: exit {exit_code} with a node OOM event"
        )
    if facts.input_modified:
        return Classified("guest_failed", True, "guest_failed: input-modified: /in changed")
    if exit_code == 0:
        size = facts.results_bytes
        if size is not None and size > RESULTS["cap_bytes"]:
            return Classified("guest_failed", True, f"guest_failed: output-over-cap: {size}")
        return Classified("ok", True, "ok")
    if INTERRUPTED.search(stderr_tail):
        return Classified("timeout", False, f"timeout: {_last_line(stderr_tail)}")
    if memory := OUT_OF_MEMORY.search(stderr_tail):
        return Classified("guest_memory_cap", True, f"guest_memory_cap: {memory.group(0)}")
    reports = list(REPORT.finditer(stderr_tail))
    if exit_code not in GUEST_EXITS or (
        reports and GUEST_RAN not in stderr_tail[reports[-1].end() :]
    ):
        return Classified(
            "host_error", False, f"host_error: exit {exit_code}: {_detail(stderr_tail)}"
        )
    if error_file is not None:
        detail = f"{error_file['code']}: {error_file['message']}"[:DETAIL_CHARS]
        return Classified("guest_failed", error_file["deterministic"], f"guest_failed: {detail}")
    return Classified(
        "guest_failed", False, f"guest_failed: exit {exit_code}: {_last_line(stderr_tail)}"
    )


def read_error_file(scratch: Path) -> dict | None:
    """The guest's error.json when it is a regular file that meets its schema, else None."""
    path = scratch / OUTPUT / ERROR_FILE
    if not path.is_file() or path.is_symlink() or path.stat().st_size > ERROR_FILE_MAX_BYTES:
        return None
    try:
        return Error.model_validate(json.loads(path.read_bytes())).model_dump()
    except (ValueError, ValidationError):
        return None


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "no output")[:DETAIL_CHARS]


def _detail(text: str) -> str:
    """wasmtime's report on one line, since its cause comes last and a reason is one line."""
    reports = list(REPORT.finditer(text))
    report = text[reports[-1].start() :] if reports else text
    return " ".join(report.split())[-DETAIL_CHARS:] or "no output"
