"""upgrade_for_replay on the test-only type of the read tests, and on the catalogue."""

import json
from datetime import date
from pathlib import Path

import pytest
from wdf_compat import Registry, upgrade_for_replay
from wdf_events import EventTooLarge, SchemaInvalid, UnsupportedMajor
from wdf_events.metrics import registry as metrics
from wdf_events.profile import CONTRACT_DIR

FIXTURES = Path(__file__).parent / "fixtures/schemas"
INSIDE_WINDOW = date(2026, 2, 15)
BASE = json.loads((CONTRACT_DIR / "vectors/types/job.failed/valid.json").read_text())


def upgrade_v1(document: dict) -> dict:
    return {
        **document,
        "type": "dev.wdf.job.fixture.v2",
        "dataschema": "urn:wdf:schema:job.fixture:2.0",
        "data": {"name": document["data"]["label"]},
    }


REGISTRY = Registry(
    FIXTURES,
    {("job.fixture", 1): "2026-01-01", ("job.fixture", 2): "2026-02-01"},
    {("job.fixture", 1): upgrade_v1},
)


def event_bytes(major: int, pretty: bool = False, **data) -> bytes:
    document = {
        **BASE,
        "type": f"dev.wdf.job.fixture.v{major}",
        "dataschema": f"urn:wdf:schema:job.fixture:{major}.0",
        "data": data,
    }
    if pretty:
        return json.dumps(document, indent=2).encode()
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def upgraded_count() -> int:
    return metrics.count("wdf_event_upgraded_total", type="job.fixture", from_major="1")


def test_the_previous_major_returns_as_canonical_bytes_of_the_current_major():
    before = upgraded_count()
    out = upgrade_for_replay(event_bytes(1, label="a"), REGISTRY, INSIDE_WINDOW)
    document = json.loads(out)
    assert (document["type"], document["data"]) == ("dev.wdf.job.fixture.v2", {"name": "a"})
    assert (document["id"], document["source"], document["time"]) == (
        BASE["id"],
        BASE["source"],
        BASE["time"],
    )
    assert out == json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    assert upgraded_count() == before + 1


def test_the_current_major_returns_byte_identical_even_when_not_canonical():
    before = upgraded_count()
    data = event_bytes(2, pretty=True, name="a")
    assert upgrade_for_replay(data, REGISTRY, INSIDE_WINDOW) == data
    assert upgraded_count() == before


def test_two_majors_back_raises_unsupported_major(tmp_path):
    (tmp_path / "job.fixture").mkdir()
    for version in ("1.0", "2.0", "3.0"):
        (tmp_path / f"job.fixture/{version}.json").write_text(
            (FIXTURES / "job.fixture/2.0.json").read_text()
        )
    registry = Registry(tmp_path, {("job.fixture", 3): "2026-02-01"}, REGISTRY.upgrades)
    with pytest.raises(UnsupportedMajor) as raised:
        upgrade_for_replay(event_bytes(1, name="a"), registry, INSIDE_WINDOW)
    assert raised.value.dlq_reason == "UNSUPPORTED_VERSION"


def test_an_invalid_event_raises_schema_invalid_and_returns_nothing():
    for data in (event_bytes(1, name="a"), b"{", event_bytes(2, label="a")):
        with pytest.raises(SchemaInvalid) as raised:
            upgrade_for_replay(data, REGISTRY, INSIDE_WINDOW)
        assert raised.value.dlq_reason == "MALFORMED"


def test_an_upgrade_that_returns_invalid_data_returns_no_partial_bytes():
    registry = Registry(
        FIXTURES, REGISTRY.introduced, {("job.fixture", 1): lambda d: {**upgrade_v1(d), "data": {}}}
    )
    with pytest.raises(SchemaInvalid):
        upgrade_for_replay(event_bytes(1, label="a"), registry, INSIDE_WINDOW)


def test_an_upgrade_over_the_size_budget_raises_event_too_large():
    registry = Registry(
        FIXTURES,
        REGISTRY.introduced,
        {("job.fixture", 1): lambda d: {**upgrade_v1(d), "data": {"name": "x" * 70_000}}},
    )
    with pytest.raises(EventTooLarge) as raised:
        upgrade_for_replay(event_bytes(1, label="a"), registry, INSIDE_WINDOW)
    assert raised.value.dlq_reason == "MALFORMED"


def test_the_retired_previous_major_raises_unsupported_major():
    with pytest.raises(UnsupportedMajor) as raised:
        upgrade_for_replay(event_bytes(1, label="a"), REGISTRY, date(2026, 4, 1))
    assert raised.value.reason == "retired-major"
