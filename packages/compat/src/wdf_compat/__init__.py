"""Compatibility rules of the event contract: the N-1 reader, the upgrade hook and the window."""

from .evolution import breaking_changes
from .reader import CATALOGUE, Registry, read_event
from .window import check_catalogue, readable_days

__all__ = [
    "CATALOGUE",
    "Registry",
    "breaking_changes",
    "check_catalogue",
    "read_event",
    "readable_days",
]
