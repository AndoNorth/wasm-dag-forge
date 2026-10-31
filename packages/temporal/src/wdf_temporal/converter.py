"""A payload converter that keeps the canonical bytes of an event in the Temporal history."""

import json
from typing import Any

from pydantic import BaseModel
from temporalio.api.common.v1 import Payload
from temporalio.converter import (
    CompositePayloadConverter,
    DataConverter,
    DefaultPayloadConverter,
    JSONPlainPayloadConverter,
)
from wdf_events import Event, from_bytes, to_bytes

# The metadata encoding of every payload this converter writes, so the Temporal UI and `json.loads`
# read the data as it is.
ENCODING = "json/plain"


def _is_model(type_hint: Any) -> bool:
    return isinstance(type_hint, type) and issubclass(type_hint, BaseModel)


class ModelJSONPayloadConverter(JSONPlainPayloadConverter):
    """`json/plain` for the platform's models, and the stock behaviour for any other value.

    An event is stored as `to_bytes(event)`, so a history holds exactly the bytes that the event
    store and the broker carry.
    Another model, such as the job params block, is stored as compact sorted JSON.
    """

    def to_payload(self, value: Any) -> Payload | None:
        if isinstance(value, Event):
            data = to_bytes(value)
        elif isinstance(value, BaseModel):
            document = value.model_dump(mode="json")
            data = json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        else:
            return super().to_payload(value)
        return Payload(metadata={"encoding": ENCODING.encode()}, data=data)

    def from_payload(self, payload: Payload, type_hint: type | None = None) -> Any:
        if type_hint is Event:
            return from_bytes(payload.data)
        if _is_model(type_hint):
            return type_hint.model_validate_json(payload.data)
        return super().from_payload(payload, type_hint)


class EventPayloadConverter(CompositePayloadConverter):
    def __init__(self) -> None:
        super().__init__(
            *(
                ModelJSONPayloadConverter() if c.encoding == ENCODING else c
                for c in DefaultPayloadConverter.default_encoding_payload_converters
            )
        )


event_data_converter = DataConverter(payload_converter_class=EventPayloadConverter)
