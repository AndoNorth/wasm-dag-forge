"""The size budget and the serializer metrics."""

import json
import logging

import pytest
from wdf_events import Event, EventTooLarge, SchemaInvalid, to_bytes
from wdf_events.codec import MAX_BYTES, WARN_BYTES
from wdf_events.metrics import registry
from wdf_events.profile import CONTRACT_DIR

KIB = 1024
ACCEPTED = CONTRACT_DIR / "vectors/accepted/valid"


def padded(size: int, filler: str = "x"):
    """An accepted event of exactly `size` serialised bytes, padded through an added field."""
    base = json.loads((ACCEPTED / "unknown-minor.json").read_text())
    base["data"]["added_optional"] = ""
    base_size = len(
        json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    room = size - base_size
    unit = len(filler.encode())
    assert room >= 0
    base["data"]["added_optional"] = filler * (room // unit) + "x" * (room % unit)
    return Event.model_validate(base)


def warnings() -> int:
    return registry.count("wdf_event_size_warnings_total")


def rejected(reason: str) -> int:
    return registry.count("wdf_event_rejected_total", reason=reason)


def test_one_byte_under_the_warning_size_is_silent(caplog):
    before = warnings()
    with caplog.at_level(logging.WARNING):
        assert len(to_bytes(padded(WARN_BYTES - 1))) == 16 * KIB - 1
    assert warnings() == before
    assert not caplog.records


def test_the_warning_size_warns_and_counts(caplog):
    event = padded(WARN_BYTES)
    before = warnings()
    with caplog.at_level(logging.WARNING):
        assert len(to_bytes(event)) == 16 * KIB
    assert warnings() == before + 1
    (record,) = caplog.records
    assert (record.event_type, record.cloudevent_id) == (event.type, event.id)


def test_the_maximum_serialises():
    assert len(to_bytes(padded(MAX_BYTES))) == 64 * KIB


def test_one_byte_over_the_maximum_is_rejected_and_counted():
    event = padded(MAX_BYTES + 1)
    before, observed = rejected("event-too-large"), registry.histograms["wdf_event_bytes"].count
    with pytest.raises(EventTooLarge):
        to_bytes(event)
    assert rejected("event-too-large") == before + 1
    assert registry.histograms["wdf_event_bytes"].count == observed


def test_a_profile_failure_is_counted_under_its_own_reason():
    event = padded(2 * KIB).model_copy(update={"time": "2026-10-30T12:00:00Z"})
    before = rejected("schema-invalid")
    with pytest.raises(SchemaInvalid):
        to_bytes(event)
    assert rejected("schema-invalid") == before + 1


def test_to_bytes_observes_the_size_in_the_pinned_buckets():
    histogram = registry.histograms["wdf_event_bytes"]
    before = (histogram.count, histogram.total, list(histogram.counts))
    size = len(to_bytes(padded(3 * KIB)))
    assert (histogram.count, histogram.total) == (before[0] + 1, before[1] + size)
    bounds = histogram.bounds
    assert bounds == [256, 1024, 2048, 4096, 16384, 65536]
    gained = [after - was for after, was in zip(histogram.counts, before[2], strict=True)]
    assert gained == [int(size <= bound) for bound in bounds]


def test_non_ascii_is_counted_in_bytes_and_not_characters():
    event = padded(MAX_BYTES, filler="é")
    data = to_bytes(event)
    assert len(data) == MAX_BYTES
    assert len(data.decode()) < MAX_BYTES
    with pytest.raises(EventTooLarge):
        to_bytes(padded(MAX_BYTES + 1, filler="é"))
