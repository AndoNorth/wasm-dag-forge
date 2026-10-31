"""The result ready event: one result reference of kind result or summary."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from wdf_events import SchemaInvalid, from_bytes
from wdf_events.profile import CONTRACT_DIR

DIR = CONTRACT_DIR / "vectors/types/task.result.ready"
VALID = json.loads((DIR / "valid.json").read_text())
REFERENCE = json.loads(
    (CONTRACT_DIR.parents[1] / "contracts/artifact/reference.schema.json").read_text()
)


def read(edit) -> object:
    document = copy.deepcopy(VALID)
    edit(document)
    return from_bytes(json.dumps(document).encode())


def test_the_valid_vector_reads_and_its_reference_validates():
    event = from_bytes((DIR / "valid.json").read_bytes())
    assert (event.type, event.attempt) == ("dev.wdf.task.result.ready.v1", 2)
    Draft202012Validator(REFERENCE).validate(event.data["result"])


def test_a_summary_is_a_result_too():
    def edit(d):
        d["data"]["result"].update(kind="summary")

    assert read(edit).data["result"]["kind"] == "summary"


@pytest.mark.parametrize("kind", ["source", "checkpoint", "output", "manifest"])
def test_other_kinds_are_not_a_result(kind):
    with pytest.raises(SchemaInvalid):
        read(lambda d: d["data"]["result"].update(kind=kind))


@pytest.mark.parametrize("edit_name", ["producer", "result"])
def test_a_missing_producer_or_reference_is_rejected(edit_name):
    def edit(d):
        (d["data"]["result"] if edit_name == "producer" else d["data"]).pop(edit_name)

    with pytest.raises(SchemaInvalid):
        read(edit)


def test_inline_bytes_and_a_second_reference_are_rejected():
    with pytest.raises(SchemaInvalid):
        read(lambda d: d["data"]["result"].update(data="AAAA"))
    with pytest.raises(SchemaInvalid):
        read(lambda d: d["data"].update(results=[d["data"]["result"]]))


def test_a_uri_outside_the_buckets_is_rejected():
    with pytest.raises(SchemaInvalid):
        read(lambda d: d["data"]["result"].update(uri="s3://elsewhere/x"))
