"""Event vectors against the library and the loader, and the vendored schema against its hash."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft7Validator, validate
from wdf_events import EventTooLarge, SchemaInvalid, UnsupportedMajor, from_bytes, to_bytes
from wdf_events.errors import EventError
from wdf_events.models import ManifestReference
from wdf_events.profile import CONTRACT_DIR

ROOT = CONTRACT_DIR.parents[1]
VALID = sorted((CONTRACT_DIR / "vectors/accepted/valid").glob("*.json"))
INVALID = sorted((CONTRACT_DIR / "vectors/accepted/invalid").glob("*.json"))
CHECK = CONTRACT_DIR / "check.sh"


def reason_of(path: Path) -> str:
    return path.stem.split("--")[0]


def load(path: Path):
    return json.loads(path.read_text())


def test_vectors_exist():
    assert len(VALID) >= 3
    assert len(INVALID) >= 20


def test_vendored_cloudevents_schema_matches_its_record():
    record = dict(
        line.split(": ", 1)
        for line in (CONTRACT_DIR / "profile/cloudevents.source.yaml").read_text().splitlines()
        if line and not line.startswith(("---", "#"))
    )
    vendored = (CONTRACT_DIR / "profile/cloudevents.json").read_bytes()
    assert hashlib.sha256(vendored).hexdigest() == record["sha256"]
    assert record["tag"] == "v1.0.2"
    assert len(record["commit"]) == 40


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_vector_passes_both_schemas_and_the_library(path):
    event = load(path)
    Draft7Validator(load(CONTRACT_DIR / "profile/cloudevents.json")).validate(event)
    Draft7Validator(load(CONTRACT_DIR / "profile/profile.schema.json")).validate(event)
    assert from_bytes(path.read_bytes()).id == event["id"]


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_vector_fails_with_its_own_reason(path):
    with pytest.raises(EventError) as raised:
        from_bytes(path.read_bytes())
    assert raised.value.reason == reason_of(path)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_check_sh_exits_0_for_a_valid_vector(path):
    run = subprocess.run([CHECK, path], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_check_sh_exits_1_with_a_reason_for_an_invalid_vector(path):
    run = subprocess.run([CHECK, path], capture_output=True, text=True)
    assert run.returncode == 1
    assert run.stderr.startswith(f"reason: {reason_of(path)}: "), run.stderr


def test_check_sh_exits_2_without_an_argument():
    assert subprocess.run([CHECK], capture_output=True, text=True).returncode == 2


def test_unknown_minor_resolves_to_the_highest_known_minor_and_keeps_the_added_field():
    event = from_bytes((CONTRACT_DIR / "vectors/accepted/valid/unknown-minor.json").read_bytes())
    assert event.dataschema.endswith(":1.7")
    assert "added_optional" in event.data


def test_accepted_manifest_validates_against_the_reference_schema():
    schema = load(ROOT / "contracts/artifact/reference.schema.json")
    for path in VALID:
        validate(load(path)["data"]["manifest"], schema)
    assert set(ManifestReference.model_fields) <= set(schema["properties"])
    assert set(ManifestReference.model_fields) == set(schema["required"])


def test_the_three_typed_errors_carry_a_dlq_reason():
    valid = (CONTRACT_DIR / "vectors/accepted/valid/accepted.json").read_bytes()
    padded = valid + b" " * 70_000
    with pytest.raises(EventTooLarge) as large:
        from_bytes(padded)
    with pytest.raises(UnsupportedMajor) as major:
        from_bytes((CONTRACT_DIR / "vectors/accepted/invalid/unsupported-major.json").read_bytes())
    with pytest.raises(SchemaInvalid) as invalid:
        from_bytes(b"[]")
    assert (large.value.dlq_reason, major.value.dlq_reason, invalid.value.dlq_reason) == (
        "MALFORMED",
        "UNSUPPORTED_VERSION",
        "MALFORMED",
    )


@pytest.mark.parametrize("data", [b"not json", b"\xff\xfe", b'{"a": NaN}'])
def test_undecodable_bytes_are_schema_invalid(data):
    with pytest.raises(SchemaInvalid) as raised:
        from_bytes(data)
    assert raised.value.reason == "malformed-json"


def test_round_trip_is_byte_stable_compact_and_small():
    raw = (CONTRACT_DIR / "vectors/accepted/valid/accepted.json").read_bytes()
    event = from_bytes(raw)
    first, second = to_bytes(event), to_bytes(event)
    assert first == second
    assert from_bytes(first) == event
    assert len(first) < 2048
    assert b": " not in first and b"\n" not in first
    assert json.loads(first) == json.loads(raw)
    assert (
        first
        == json.dumps(
            json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    )


def test_to_bytes_rejects_an_event_that_fails_the_profile():
    event = from_bytes((CONTRACT_DIR / "vectors/accepted/valid/accepted.json").read_bytes())
    with pytest.raises(SchemaInvalid) as raised:
        to_bytes(event.model_copy(update={"time": "2026-10-30T12:00:00Z"}))
    assert raised.value.reason == "bad-time"
