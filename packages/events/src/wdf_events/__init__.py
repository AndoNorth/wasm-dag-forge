"""Platform event models, canonical serializer and checks."""

from .codec import MAX_BYTES, format_time, from_bytes, to_bytes
from .errors import EventError, EventTooLarge, SchemaInvalid, UnsupportedMajor
from .models import Event

__all__ = [
    "MAX_BYTES",
    "Event",
    "EventError",
    "EventTooLarge",
    "SchemaInvalid",
    "UnsupportedMajor",
    "format_time",
    "from_bytes",
    "to_bytes",
]
