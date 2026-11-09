"""The class of a finished guest run, from the simulate pool of the exit class table."""

from typing import Any, NamedTuple

from ._stub import not_implemented

STUBBED = ("classify",)


class Classified(NamedTuple):
    cls: str
    deterministic: bool
    reason: str


def classify(
    exit_code: int, stderr_tail: str, error_file: dict | None, runner_facts: Any
) -> Classified:
    raise not_implemented("R")
