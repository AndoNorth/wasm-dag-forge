"""Platform event models, canonical serializer and checks."""

from .build import build_event
from .codec import MAX_BYTES, format_time, from_bytes, to_bytes
from .errors import EventError, EventTooLarge, SchemaInvalid, UnsupportedMajor
from .models import Event
from .store import to_event_row

__all__ = [
    "MAX_BYTES",
    "Event",
    "EventError",
    "EventTooLarge",
    "SchemaInvalid",
    "UnsupportedMajor",
    "build_event",
    "format_time",
    "from_bytes",
    "to_bytes",
    "to_event_row",
]
