"""Reads an event of the current major or the one before it, upgrading the older one."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from wdf_events import Event, SchemaInvalid, UnsupportedMajor, from_bytes
from wdf_events.metrics import registry as metrics
from wdf_events.models import INTRODUCED
from wdf_events.profile import CONTRACT_DIR, TYPE_RE, check_event, known_versions

from . import window

# The attributes that identify a fact, which an upgrade never changes so the store still dedups.
IDENTITY = ("id", "source", "time")

Upgrade = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Registry:
    """The schema files, the introduction date of each major and the upgrade of each major step.

    `upgrades[(name, major)]` turns a decoded event of that major into a decoded event of the next.
    """

    schemas_dir: Path
    introduced: Mapping[tuple[str, int], str]
    upgrades: Mapping[tuple[str, int], Upgrade] = field(default_factory=dict)

    def current(self, name: str) -> int:
        return max(major for major, _ in known_versions(name, self.schemas_dir))


# One upgrade function per major step is added here, next to the model of the new major.
UPGRADES: dict[tuple[str, int], Upgrade] = {}

CATALOGUE = Registry(CONTRACT_DIR / "schemas", INTRODUCED, UPGRADES)


def _upgrade(event: Event, registry: Registry, today: date) -> Event:
    name = TYPE_RE.fullmatch(event.type)["name"]
    major = int(TYPE_RE.fullmatch(event.type)["major"])
    current = registry.current(name)
    if major == current:
        return event
    if major != current - 1:
        raise UnsupportedMajor(
            "unsupported-major",
            f"{event.type} is {current - major} majors behind {name} v{current}",
        )
    introduced = registry.introduced.get((name, current))
    if introduced and window.retired(introduced, today):
        raise UnsupportedMajor(
            "retired-major",
            f"{event.type} left its window {window.readable_days()} days after {introduced}",
        )
    upgrade = registry.upgrades.get((name, major))
    if upgrade is None:
        raise UnsupportedMajor("no-upgrade", f"{name} has no upgrade from v{major}")
    document = upgrade(event.model_dump(exclude_none=True))
    check_event(document, registry.schemas_dir)
    if any(document.get(key) != getattr(event, key) for key in IDENTITY):
        raise SchemaInvalid("upgrade-changed-identity", f"{event.type} upgrade changed {IDENTITY}")
    metrics.inc("wdf_event_upgraded_total", type=name, from_major=str(major))
    return Event.model_validate(document)


def read_event(data: bytes, registry: Registry = CATALOGUE, today: date | None = None) -> Event:
    """The event in `data` at the current major of its type.

    An event of the previous major is upgraded while its window is open, and raises
    UnsupportedMajor after that.
    An event of any other major raises UnsupportedMajor, and an invalid one raises SchemaInvalid.
    `today` is the day the window is measured on, and defaults to today in UTC.
    """
    event = from_bytes(data, registry.schemas_dir)
    return _upgrade(event, registry, today or datetime.now(UTC).date())
