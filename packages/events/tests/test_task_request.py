"""The task request event and the worker-side check."""

import copy
import json
from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator
from wdf_events import (
    EventTooLarge,
    SchemaInvalid,
    UnsupportedMajor,
    build_event,
    check_task_request,
    to_bytes,
)
from wdf_events.profile import CONTRACT_DIR

VECTORS = CONTRACT_DIR / "vectors/types"
VALID = json.loads((VECTORS / "task.requested/valid.json").read_text())
REFERENCE = json.loads(
    (CONTRACT_DIR.parents[1] / "contracts/artifact/reference.schema.json").read_text()
)


def canonical(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def changed(edit) -> bytes:
    document = copy.deepcopy(VALID)
    edit(document)
    return canonical(document)


def test_the_valid_vector_gives_a_typed_request():
    request = check_task_request((VECTORS / "task.requested/valid.json").read_bytes())
    assert (request.taskid, request.tenant) == ("sim", "acme")
    assert request.data.task_type == "simulate"
    assert (request.data.cpu_cores, request.data.memory_mib) == (1.5, 1024)
    assert request.data.inputs[0].kind == "source"
    assert request.data.resume_checkpoint.producer.attempt == "r0a1"
    assert request.data.deadline == "2026-10-30T13:00:00.000000Z"


def test_every_reference_of_the_vector_validates_against_the_reference_schema():
    validator = Draft202012Validator(REFERENCE)
    data = VALID["data"]
    for reference in [*data["inputs"], data["resume_checkpoint"]]:
        validator.validate(reference)


def test_the_invalid_vector_is_rejected():
    with pytest.raises(SchemaInvalid):
        check_task_request((VECTORS / "task.requested/invalid.json").read_bytes())


def test_a_wrong_major_is_unsupported():
    def edit(d):
        d["type"] = "dev.wdf.task.requested.v2"
        d["dataschema"] = "urn:wdf:schema:task.requested:2.0"

    with pytest.raises(UnsupportedMajor):
        check_task_request(changed(edit))


@pytest.mark.parametrize("field", ["cpu_cores", "memory_mib"])
def test_a_missing_cpu_or_memory_is_named(field):
    with pytest.raises(SchemaInvalid) as raised:
        check_task_request(changed(lambda d: d["data"].pop(field)))
    assert (raised.value.reason, field in raised.value.detail) == ("bad-data", True)


def test_an_unknown_task_type_is_rejected():
    with pytest.raises(SchemaInvalid) as raised:
        check_task_request(changed(lambda d: d["data"].update(task_type="render")))
    assert raised.value.reason == "bad-data"


@pytest.mark.parametrize("field", ["data", "bytes", "content"])
def test_a_reference_with_inline_bytes_is_rejected(field):
    with pytest.raises(SchemaInvalid) as raised:
        check_task_request(changed(lambda d: d["data"]["inputs"][0].update({field: "AAAA"})))
    assert raised.value.reason == "bad-data"


def test_a_reference_must_match_its_kind_on_the_producer():
    with pytest.raises(SchemaInvalid):
        check_task_request(changed(lambda d: d["data"]["inputs"][0].update(kind="result")))
    with pytest.raises(SchemaInvalid):
        check_task_request(
            changed(
                lambda d: d["data"]["inputs"][0].update(
                    producer=VALID["data"]["resume_checkpoint"]["producer"]
                )
            )
        )


def test_an_unknown_field_within_a_known_minor_is_rejected():
    with pytest.raises(SchemaInvalid):
        check_task_request(changed(lambda d: d["data"].update(extra_field=1)))


def test_a_newer_minor_with_an_added_optional_field_passes_and_the_field_is_ignored():
    def edit(d):
        d["dataschema"] = "urn:wdf:schema:task.requested:1.4"
        d["data"]["priority_hint"] = 3

    request = check_task_request(changed(edit))
    assert request.data.task_type == "simulate"
    assert "priority_hint" not in request.model_dump()["data"]


def test_another_event_type_is_not_a_task_request():
    with pytest.raises(SchemaInvalid) as raised:
        check_task_request((VECTORS / "task.result.ready/valid.json").read_bytes())
    assert raised.value.reason == "wrong-type"


def test_a_request_that_lists_100000_references_is_too_large():
    reference = VALID["data"]["inputs"][0]
    data = {**VALID["data"], "inputs": [reference] * 100_000}
    event = build_event(
        "task.requested",
        source="/wdf/workflow",
        tenant="acme",
        jobid=VALID["jobid"],
        priorityclass="standard",
        jobclass="aggregate",
        traceparent=VALID["traceparent"],
        time=datetime(2026, 10, 30, 12, tzinfo=UTC),
        taskid="agg",
        data=data,
    )
    with pytest.raises(EventTooLarge):
        to_bytes(event)
