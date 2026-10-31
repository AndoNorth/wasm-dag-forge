"""The event history row of an event: the key pair and the verbatim body."""

from datetime import UTC, datetime

from .codec import to_bytes
from .models import Event


def to_event_row(event: Event) -> tuple[str, datetime, bytes]:
    """`(<source>#<id>, time, bytes)`, the event id and event time columns and the body.

    The body is the canonical bytes, so a rebuilt event gives an equal row, and `time` carries
    microseconds so that it survives a timestamptz round trip.
    """
    moment = datetime.strptime(event.time, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return f"{event.source}#{event.id}", moment, to_bytes(event)
