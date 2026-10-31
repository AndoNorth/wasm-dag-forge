"""read_event on a test-only type with two majors, so the catalogue needs no second major."""

import json
from datetime import date
from pathlib import Path

import pytest
from wdf_compat import Registry, read_event, window
from wdf_events import SchemaInvalid, UnsupportedMajor
from wdf_events.metrics import registry as metrics
from wdf_events.profile import CONTRACT_DIR

FIXTURES = Path(__file__).parent / "fixtures/schemas"
V2_INTRODUCED = "2026-02-01"
INSIDE_WINDOW = date(2026, 2, 15)
OUTSIDE_WINDOW = date(2026, 3, 15)
BASE = json.loads((CONTRACT_DIR / "vectors/types/job.failed/valid.json").read_text())


def upgrade_v1(document: dict) -> dict:
    data = {"name": document["data"]["label"]}
    return {
        **document,
        "type": "dev.wdf.job.fixture.v2",
        "dataschema": "urn:wdf:schema:job.fixture:2.0",
        "data": data,
    }


REGISTRY = Registry(
    FIXTURES,
    {("job.fixture", 1): "2026-01-01", ("job.fixture", 2): V2_INTRODUCED},
    {("job.fixture", 1): upgrade_v1},
)


def event_bytes(major: int, minor: int = 0, **data) -> bytes:
    document = {
        **BASE,
        "type": f"dev.wdf.job.fixture.v{major}",
        "dataschema": f"urn:wdf:schema:job.fixture:{major}.{minor}",
        "data": data,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def upgraded_count() -> int:
    return metrics.count("wdf_event_upgraded_total", type="job.fixture", from_major="1")


def test_the_current_major_is_read_directly():
    before = upgraded_count()
    event = read_event(event_bytes(2, name="a"), REGISTRY, INSIDE_WINDOW)
    assert (event.type, event.data) == ("dev.wdf.job.fixture.v2", {"name": "a"})
    assert upgraded_count() == before


def test_the_previous_major_is_read_through_its_upgrade():
    before = upgraded_count()
    event = read_event(event_bytes(1, label="a"), REGISTRY, INSIDE_WINDOW)
    assert (event.type, event.dataschema, event.data) == (
        "dev.wdf.job.fixture.v2",
        "urn:wdf:schema:job.fixture:2.0",
        {"name": "a"},
    )
    assert (event.id, event.source, event.time) == (BASE["id"], BASE["source"], BASE["time"])
    assert upgraded_count() == before + 1


def test_a_major_after_the_current_one_is_unsupported():
    with pytest.raises(UnsupportedMajor) as raised:
        read_event(event_bytes(3, name="a"), REGISTRY, INSIDE_WINDOW)
    assert raised.value.dlq_reason == "UNSUPPORTED_VERSION"


def test_a_major_two_steps_back_is_unsupported(tmp_path):
    (tmp_path / "job.fixture").mkdir()
    for version in ("1.0", "2.0", "3.0"):
        (tmp_path / f"job.fixture/{version}.json").write_text(
            (FIXTURES / "job.fixture/2.0.json").read_text()
        )
    registry = Registry(tmp_path, {("job.fixture", 3): V2_INTRODUCED}, REGISTRY.upgrades)
    with pytest.raises(UnsupportedMajor) as raised:
        read_event(event_bytes(1, name="a"), registry, INSIDE_WINDOW)
    assert raised.value.reason == "unsupported-major"


def test_the_previous_major_is_unsupported_once_its_window_is_over():
    before = upgraded_count()
    with pytest.raises(UnsupportedMajor) as raised:
        read_event(event_bytes(1, label="a"), REGISTRY, OUTSIDE_WINDOW)
    assert raised.value.reason == "retired-major"
    assert upgraded_count() == before


def test_the_window_constants_retire_the_previous_major(monkeypatch):
    read_event(event_bytes(1, label="a"), REGISTRY, INSIDE_WINDOW)
    monkeypatch.setattr(window, "DEAD_LETTER_RETENTION_DAYS", 1)
    monkeypatch.setattr(window, "MAX_JOB_DURATION_DAYS", 1)
    with pytest.raises(UnsupportedMajor):
        read_event(event_bytes(1, label="a"), REGISTRY, INSIDE_WINDOW)


def test_a_newer_minor_with_an_added_optional_field_is_read_and_a_known_minor_rejects_it():
    event = read_event(event_bytes(2, minor=7, name="a", hint="x"), REGISTRY, INSIDE_WINDOW)
    assert event.data["name"] == "a"
    with pytest.raises(SchemaInvalid):
        read_event(event_bytes(2, name="a", hint="x"), REGISTRY, INSIDE_WINDOW)


def test_invalid_data_of_the_previous_major_is_rejected_before_its_upgrade():
    with pytest.raises(SchemaInvalid) as raised:
        read_event(event_bytes(1, name="a"), REGISTRY, INSIDE_WINDOW)
    assert raised.value.reason == "bad-data"


def test_an_upgrade_that_returns_invalid_data_is_rejected():
    registry = Registry(
        FIXTURES, REGISTRY.introduced, {("job.fixture", 1): lambda d: {**upgrade_v1(d), "data": {}}}
    )
    with pytest.raises(SchemaInvalid):
        read_event(event_bytes(1, label="a"), registry, INSIDE_WINDOW)


def test_an_upgrade_that_changes_the_identity_is_rejected():
    registry = Registry(
        FIXTURES,
        REGISTRY.introduced,
        {("job.fixture", 1): lambda d: {**upgrade_v1(d), "id": "other"}},
    )
    with pytest.raises(SchemaInvalid) as raised:
        read_event(event_bytes(1, label="a"), registry, INSIDE_WINDOW)
    assert raised.value.reason == "upgrade-changed-identity"


def test_a_previous_major_without_an_upgrade_is_unsupported():
    registry = Registry(FIXTURES, REGISTRY.introduced)
    with pytest.raises(UnsupportedMajor) as raised:
        read_event(event_bytes(1, label="a"), registry, INSIDE_WINDOW)
    assert raised.value.reason == "no-upgrade"


def test_the_catalogue_reads_what_the_event_library_reads():
    for path in sorted(CONTRACT_DIR.glob("vectors/types/*/valid.json")):
        assert read_event(path.read_bytes()).type.endswith(".v1")
