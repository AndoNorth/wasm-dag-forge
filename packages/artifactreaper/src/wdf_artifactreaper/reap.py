"""One idempotent reaper run over the six passes."""

from datetime import datetime
from typing import Any

from ._stub import not_implemented

STUBBED = ("ReapReport", "reap_once")


class ReapReport:
    """The counters of a run: what was handled, kept, pruned, expired, tombstoned and purged."""

    def __init__(self, *args: object, **kwargs: object):
        raise not_implemented("P")


def reap_once(conn: Any, client: Any, now: datetime, windows: Any) -> ReapReport:
    raise not_implemented("P")
