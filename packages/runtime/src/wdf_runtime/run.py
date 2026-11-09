"""One guest run: the warm-up when the cache lacks the component, a fresh copy of input.json at /in,
wasmtime, the re-hash of the copy, and the class."""

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .classify import RESULTS, Classified, Facts, classify, read_error_file
from .command import GUEST_INPUT, INPUT_FILE, OUTPUT, STAGING, CapRefused, command, warm_up
from .heartbeat import beats

TAIL_BYTES = STAGING["log_tail"]["cap_bytes"]


@dataclass(frozen=True)
class Ran:
    classified: Classified
    heartbeats: int
    # The last bytes of the guest's combined stdout and stderr.
    tail: bytes


def run(
    component: Path,
    input_file: Path,
    scratch: Path,
    memory_mib: int,
    timeout_s: float | None,
    cache_dir: Path | None = None,
) -> Ran:
    """Runs the guest against scratch, whose output directory receives what it writes to /out."""
    try:
        argv = command(component, scratch, memory_mib, timeout_s, cache_dir)
    except CapRefused as refused:
        return Ran(classify(None, "", None, Facts(cap_refused=str(refused))), 0, b"")
    if cache_dir is not None and component.is_file() and not warmed(component, cache_dir):
        warm(component, cache_dir)
    digest = copy_input(input_file, scratch)
    (scratch / OUTPUT).mkdir(parents=True, exist_ok=True)
    try:
        with subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        ) as process:
            count, output = _read(process.stdout)
        exit_code = process.returncode
    except OSError as error:
        return Ran(classify(None, "", None, Facts(host_error=str(error))), 0, b"")
    results = scratch / OUTPUT / RESULTS["file"]
    facts = Facts(
        input_modified=input_digest(scratch) != digest,
        results_bytes=results.stat().st_size if results.is_file() else None,
    )
    text = output.decode(errors="replace")
    return Ran(classify(exit_code, text, read_error_file(scratch), facts), count, output)


def copy_input(input_file: Path, scratch: Path) -> str:
    """A fresh /in directory holding only a copy of input.json, and its digest."""
    guest_input = scratch / GUEST_INPUT
    shutil.rmtree(guest_input, ignore_errors=True)
    guest_input.mkdir(parents=True)
    shutil.copyfile(input_file, guest_input / INPUT_FILE)
    return input_digest(scratch)


def input_digest(scratch: Path) -> str:
    """The SHA-256 over every name and file under /in, since wasmtime cannot mount it read only."""
    digest = hashlib.sha256()
    root = scratch / GUEST_INPUT
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        if path.is_file() and not path.is_symlink():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def warmed(component: Path, cache_dir: Path) -> bool:
    return _marker(component, cache_dir).exists()


def warm(component: Path, cache_dir: Path) -> None:
    """The warm-up in its own process, so the task's guest never runs where wasmtime compiled.

    A failed warm-up is not recorded, and the task then compiles and reports its own failure.
    """
    with tempfile.TemporaryDirectory(prefix="wdf-warm-up-") as scratch:
        argv = warm_up(component, Path(scratch), cache_dir)
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True)
    if done.returncode == 0:
        marker = _marker(component, cache_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()


def _marker(component: Path, cache_dir: Path) -> Path:
    with open(component, "rb") as f:
        return cache_dir / "warm" / hashlib.file_digest(f, "sha256").hexdigest()


def _read(stream) -> tuple[int, bytes]:
    """Counts the heartbeats and keeps the tail, reading lines of at most the tail's cap."""
    tail, count = bytearray(), 0
    for line in iter(lambda: stream.readline(TAIL_BYTES), b""):
        tail += line
        if len(tail) > 2 * TAIL_BYTES:
            del tail[:-TAIL_BYTES]
        count += sum(1 for _ in beats([line.decode(errors="replace")]))
    return count, bytes(tail[-TAIL_BYTES:])
