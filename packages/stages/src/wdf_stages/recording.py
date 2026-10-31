"""A wrapper that records which attributes and data fields of an inbound event a stage reads."""

from collections.abc import Iterator, Mapping
from typing import Any

from wdf_events.models import Event

ATTRIBUTES = frozenset(Event.model_fields) - {"data"}


def type_name(event_type: str) -> str:
    """`dev.wdf.job.accepted.v1` as the contract names it, `job.accepted`."""
    return event_type.removeprefix("dev.wdf.").rsplit(".v", 1)[0]


class _Data(Mapping):
    def __init__(self, name: str, data: dict[str, Any], reads: set[tuple[str, str]]):
        self._name, self._data, self._reads = name, data, reads

    def __getitem__(self, key: str) -> Any:
        self._reads.add((self._name, key))
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class Recorded:
    """An inbound event whose reads are recorded.

    `name` is the type the message was delivered as, which is routing and not a read.
    Every other context attribute is a read, and so is every key looked up in `data`.
    """

    def __init__(self, event: Event):
        self._event = event
        self.name = type_name(event.type)
        self.attribute_reads: set[str] = set()
        self.data_reads: set[tuple[str, str]] = set()

    def __getattr__(self, attribute: str) -> Any:
        if attribute not in ATTRIBUTES:
            raise AttributeError(attribute)
        self.attribute_reads.add(attribute)
        return getattr(self._event, attribute)

    @property
    def data(self) -> Mapping[str, Any]:
        return _Data(self.name, self._event.data, self.data_reads)
