"""The wasmtime command lines of a guest run and of the warm-up that fills the module cache."""

from pathlib import Path

from ._stub import not_implemented

STUBBED = ("command", "warm_up")


def command(
    component: Path,
    scratch: Path,
    memory_mib: int,
    timeout_s: float | None,
    cache_dir: Path | None = None,
) -> list[str]:
    raise not_implemented("R")


def warm_up(component: Path, scratch: Path, cache_dir: Path) -> list[str]:
    raise not_implemented("R")
