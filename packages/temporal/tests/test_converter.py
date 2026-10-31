"""The converter without a server: canonical bytes, and why the stock converter is not enough."""

from pathlib import Path

import pytest
from temporalio.converter import DefaultPayloadConverter
from wdf_events import Event, from_bytes, to_bytes
from wdf_events.models import JobParams
from wdf_temporal import ENCODING, EventPayloadConverter

VECTORS = Path(__file__).resolve().parents[3] / "contracts/events/vectors/accepted/valid"
UNICODE = from_bytes((VECTORS / "tracestate-and-unicode.json").read_bytes())
PLAIN = from_bytes((VECTORS / "accepted.json").read_bytes())


@pytest.mark.parametrize("event", [PLAIN, UNICODE])
def test_an_event_round_trips_through_the_canonical_bytes(event):
    converter = EventPayloadConverter()
    [payload] = converter.to_payloads([event])
    assert payload.data == to_bytes(event)
    assert payload.metadata["encoding"] == ENCODING.encode()
    assert converter.from_payloads([payload], [Event]) == [event]


def test_the_stock_converter_stores_a_unicode_event_as_other_bytes():
    [payload] = DefaultPayloadConverter().to_payloads([UNICODE.model_dump(exclude_none=True)])
    assert payload.data != to_bytes(UNICODE)


def test_the_params_block_round_trips_and_other_values_keep_the_stock_form():
    converter = EventPayloadConverter()
    params = JobParams(seed=1, parameters={"b": 2, "a": "naïve"})
    [payload] = converter.to_payloads([params])
    assert (
        payload.data == '{"class_override":null,"parameters":{"a":"naïve","b":2},"seed":1}'.encode()
    )
    assert converter.from_payloads([payload], [JobParams]) == [params]
    [payload] = converter.to_payloads([[1, 2]])
    assert converter.from_payloads([payload], [list]) == [[1, 2]]
