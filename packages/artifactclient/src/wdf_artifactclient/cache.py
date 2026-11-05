"""The compile cache client over the artifact index."""

from typing import Any

from ._stub import not_implemented

STUBBED = (
    "Hit",
    "Miss",
    "Won",
    "Lost",
    "WasmGone",
    "lookup",
    "use",
    "record",
    "invalidate",
    "touch",
)


class Hit:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("K")


class Miss:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("K")


class Won:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("K")


class Lost:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("K")


class WasmGone:
    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("K")


def lookup(conn: Any, client: Any, key: str) -> Hit | Miss:
    raise not_implemented("K")


def use(conn: Any, hit: Hit, job_id: str) -> None:
    raise not_implemented("K")


def record(conn: Any, wasm: Any, build_info: Any, key: str, versions: Any) -> Won | Lost | WasmGone:
    raise not_implemented("K")


def invalidate(conn: Any, key: str, at: Any) -> None:
    raise not_implemented("K")


def touch(conn: Any, key: str, at: Any) -> None:
    raise not_implemented("K")
