"""The read path: byte-stable round trips, the registry of schema files and the reader rule."""

import json
import random
import uuid

import pytest
from wdf_events import SchemaInvalid, UnsupportedMajor, from_bytes, to_bytes
from wdf_events.profile import CONTRACT_DIR

VECTORS = sorted(CONTRACT_DIR.glob("vectors/accepted/valid/*.json")) + sorted(
    CONTRACT_DIR.glob("vectors/types/*/valid.json")
)
ACCEPTED = json.loads((CONTRACT_DIR / "vectors/accepted/valid/accepted.json").read_text())


def canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: p.stem)
def test_a_canonical_vector_survives_a_round_trip(path):
    data = canonical(json.loads(path.read_text()))
    assert to_bytes(from_bytes(data)) == data


def random_text(rng: random.Random, low: int, high: int) -> str:
    # Code points outside the surrogate range, so the text encodes as UTF-8.
    alphabet = [
        chr(c) for c in (*range(0x20, 0x7F), *range(0xA1, 0x250), *range(0x3041, 0x3090), 0x1F600)
    ]
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(low, high)))


def random_event(rng: random.Random) -> dict:
    job = str(uuid.UUID(int=rng.getrandbits(128), version=5))
    document = json.loads(json.dumps(ACCEPTED))
    document.update(
        id=str(uuid.UUID(int=rng.getrandbits(128), version=5)),
        jobid=job,
        subject=job,
        tenant=rng.choice(["acme", "t-" + str(rng.randint(0, 999)), "x"]),
        time=f"2026-10-30T12:00:00.{rng.randint(0, 999999):06d}Z",
        traceparent=f"00-{rng.getrandbits(128):032x}-{rng.getrandbits(64):016x}-01",
    )
    document["data"]["submission_key"] = random_text(rng, 1, 256)
    document["data"]["batch_id"] = str(uuid.UUID(int=rng.getrandbits(128), version=5))
    return document


def test_generated_events_survive_a_round_trip():
    rng = random.Random(4)
    for _ in range(200):
        data = canonical(random_event(rng))
        assert to_bytes(from_bytes(data)) == data


def test_a_known_major_with_an_unknown_type_is_an_unknown_type():
    document = {**ACCEPTED, "type": "dev.wdf.job.nosuchthing.v1"}
    with pytest.raises(SchemaInvalid) as raised:
        from_bytes(canonical(document))
    assert raised.value.reason == "unknown-type"


def test_an_unknown_major_is_unsupported_before_the_data_is_opened():
    document = {
        **ACCEPTED,
        "type": "dev.wdf.job.accepted.v2",
        "dataschema": "urn:wdf:schema:job.accepted:2.0",
    }
    document["data"] = {"nothing": "the v1 schema would accept"}
    with pytest.raises(UnsupportedMajor):
        from_bytes(canonical(document))


def test_data_failing_the_named_schema_is_schema_invalid():
    document = json.loads(json.dumps(ACCEPTED))
    del document["data"]["batch_id"]
    with pytest.raises(SchemaInvalid) as raised:
        from_bytes(canonical(document))
    assert raised.value.reason == "bad-data"


def test_an_unknown_minor_ignores_added_fields_and_a_known_minor_rejects_them():
    unknown = json.loads(json.dumps(ACCEPTED))
    unknown["dataschema"] = "urn:wdf:schema:job.accepted:1.9"
    unknown["data"]["added_optional"] = 1
    assert from_bytes(canonical(unknown)).data["added_optional"] == 1
    known = json.loads(json.dumps(ACCEPTED))
    known["data"]["added_optional"] = 1
    with pytest.raises(SchemaInvalid):
        from_bytes(canonical(known))
