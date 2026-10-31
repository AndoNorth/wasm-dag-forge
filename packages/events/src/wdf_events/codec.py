"""Canonical bytes of an event: compact UTF-8 JSON with sorted keys."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .errors import EventError, EventTooLarge, SchemaInvalid
from .metrics import registry
from .models import Event
from .profile import check_event

log = logging.getLogger(__name__)

# The CloudEvents specification obliges intermediaries to forward only this many bytes.
MAX_BYTES = 64 * 1024
# An event this size or larger is still sent, and hints at a payload that wants a reference.
WARN_BYTES = 16 * 1024


def format_time(moment: datetime) -> str:
    """RFC 3339 UTC with six fractional digits, so the store key survives a timestamptz trip."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _no_constants(token: str) -> Any:
    raise SchemaInvalid("malformed-json", f"{token} is not valid JSON")


def to_bytes(event: Event) -> bytes:
    try:
        document = event.model_dump(exclude_none=True)
        check_event(document)
        encoded = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if len(encoded) > MAX_BYTES:
            raise EventTooLarge(
                "event-too-large", f"{len(encoded)} bytes, the limit is {MAX_BYTES}"
            )
    except EventError as exc:
        registry.inc("wdf_event_rejected_total", reason=exc.metric_reason)
        raise
    registry.observe("wdf_event_bytes", len(encoded))
    if len(encoded) >= WARN_BYTES:
        registry.inc("wdf_event_size_warnings_total")
        log.warning(
            "event is %d bytes, the warning size is %d",
            len(encoded),
            WARN_BYTES,
            extra={"event_type": event.type, "cloudevent_id": event.id},
        )
    return encoded


def from_bytes(data: bytes, schemas_dir: Path | None = None) -> Event:
    if len(data) > MAX_BYTES:
        raise EventTooLarge("event-too-large", f"{len(data)} bytes, the limit is {MAX_BYTES}")
    try:
        document = json.loads(data.decode(), parse_constant=_no_constants)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaInvalid("malformed-json", str(exc)) from None
    check_event(document, schemas_dir)
    try:
        return Event.model_validate(document)
    except ValidationError as exc:
        raise SchemaInvalid("bad-event", str(exc)) from None
