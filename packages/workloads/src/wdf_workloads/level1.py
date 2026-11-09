"""The level 1 workloads: noop, cpu_burn, mem_hold and io_churn, with heartbeats."""

from pathlib import Path

from ._stub import not_implemented

STUBBED = ("noop", "cpu_burn", "mem_hold", "io_churn")


def noop(seed: int, args: dict, out: Path) -> None:
    raise not_implemented("W")


def cpu_burn(seed: int, args: dict, out: Path) -> None:
    raise not_implemented("W")


def mem_hold(seed: int, args: dict, out: Path) -> None:
    raise not_implemented("W")


def io_churn(seed: int, args: dict, out: Path) -> None:
    raise not_implemented("W")


WORKLOADS = {"noop": noop, "cpu_burn": cpu_burn, "mem_hold": mem_hold, "io_churn": io_churn}
