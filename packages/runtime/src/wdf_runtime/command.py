"""The wasmtime command lines of a guest run and of the warm-up that fills the module cache."""

import json
import math
import os
from pathlib import Path

import yaml

from .models import CONTRACT

STUBBED = ()

MIB = 1024 * 1024
PAGE = 64 * 1024
STAGING = yaml.safe_load((CONTRACT / "staging.yaml").read_text())
OUTPUT = STAGING["directories"]["output"]
INPUT_FILE = STAGING["input_file"]
# The fresh directory under the scratch directory that holds the copy of input.json the guest sees.
GUEST_INPUT = "guest-input"
NOOP = {"args": {}, "seed": 0, "workload": "noop"}
# -W timeout compiles epoch checks into the code, so it is part of the module cache key. Every run
# passes one, so the warm-up's cache entry serves every task, and a run without a deadline gets one
# that never fires in practice.
NO_DEADLINE = "31536000s"


class CapRefused(ValueError):
    """A guest cap below the initial memory the component declares, refused before wasmtime."""


def command(
    component: Path,
    scratch: Path,
    memory_mib: int,
    timeout_s: float | None,
    cache_dir: Path | None = None,
) -> list[str]:
    """The guest run under a memory cap of memory_mib less what wasmtime itself holds."""
    # The package imports this module, so the contract loader is imported only when called, and
    # python -m wdf_runtime.contract still runs a module that is not imported yet.
    from .pins import GUEST_MIN_PAGES, GUEST_OVERHEAD_MIB

    guest_bytes = (memory_mib - GUEST_OVERHEAD_MIB) * MIB
    if guest_bytes < GUEST_MIN_PAGES * PAGE:
        raise CapRefused(
            f"{memory_mib} MiB leaves a guest cap of {max(guest_bytes, 0) // PAGE} pages, below "
            f"the {GUEST_MIN_PAGES} pages the component declares"
        )
    timeout = NO_DEADLINE if timeout_s is None else f"{math.ceil(timeout_s * 1000)}ms"
    return _wasmtime(
        component, scratch, timeout, cache_dir, ["-W", f"max-memory-size={guest_bytes}"]
    )


def warm_up(component: Path, scratch: Path, cache_dir: Path) -> list[str]:
    """A noop run with no memory cap and no deadline that compiles the component into the cache.

    It writes the noop input into the scratch directory, which the caller keeps apart from a task's.
    """
    from .contract import canonical

    guest_input = scratch / GUEST_INPUT
    guest_input.mkdir(parents=True, exist_ok=True)
    (guest_input / INPUT_FILE).write_text(canonical(NOOP))
    (scratch / OUTPUT).mkdir(parents=True, exist_ok=True)
    return _wasmtime(component, scratch, NO_DEADLINE, cache_dir, [])


def _wasmtime(component, scratch, timeout, cache_dir, flags) -> list[str]:
    """No --env and no stdin: the guest sees /in, /out and nothing else of the host."""
    argv = ["wasmtime", "run", *flags, "-W", f"timeout={timeout}"]
    if cache_dir is not None:
        argv += ["-C", "cache=y", "-C", f"cache-config={_cache_config(cache_dir)}"]
    argv += ["--dir", f"{scratch / GUEST_INPUT}::/in", "--dir", f"{scratch / OUTPUT}::/out"]
    return [*argv, os.fspath(component)]


def _cache_config(cache_dir: Path) -> Path:
    """wasmtime takes its cache directory from a config file, written beside the cache it names."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    config = cache_dir / "cache.toml"
    directory = str((cache_dir / "wasmtime").resolve())
    # A JSON string is a valid TOML basic string.
    config.write_text(f"[cache]\ndirectory = {json.dumps(directory)}\n")
    return config
