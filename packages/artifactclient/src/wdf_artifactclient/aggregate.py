"""The aggregate index object: canonical JSON Lines of the results that an aggregate task reads."""

from collections.abc import Iterable, Iterator
from typing import Any, BinaryIO

from ._stub import not_implemented

STUBBED = ("build_index", "put_index", "read_index", "open_results")


def build_index(entries: Iterable[Any], header: Any, out: BinaryIO) -> None:
    raise not_implemented("K")


def put_index(client: Any, entries: Iterable[Any], header: Any) -> Any:
    raise not_implemented("K")


def read_index(client: Any, reference: Any, tenant: str) -> Iterator[Any]:
    raise not_implemented("K")


def open_results(client: Any, entries: Iterable[Any], scratch: Any) -> Iterator[Any]:
    raise not_implemented("K")
