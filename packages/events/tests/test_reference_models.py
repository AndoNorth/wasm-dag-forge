"""The reference models take their kinds and buckets from the artifact layout."""

import copy
import json
import re
from typing import Annotated, get_args

import pytest
from pydantic import Field, ValidationError
from wdf_compat import breaking_changes
from wdf_events import layout
from wdf_events.models import (
    AttemptReference,
    CheckpointReference,
    ContentReference,
    ManifestReference,
    ResultReference,
    TaskRequestData,
    reference_models,
)
from wdf_events.profile import CONTRACT_DIR
from wdf_events.schemagen import render_reference

ROOT = CONTRACT_DIR.parents[1]
HASH = "ab" * 32
PRODUCER = {"job": "0b9c6b1e-4f0a-4f6e-9d57-1c2f4f3a8a11", "task": "sim", "attempt": "r0a1"}


def extended() -> dict:
    doc = copy.deepcopy(layout())
    doc["buckets"].append("wdf-traces")
    doc["kinds"]["sbom"] = {**doc["kinds"]["source"]}
    doc["kinds"]["trace"] = {**doc["kinds"]["output"], "bucket": "wdf-traces"}
    return doc


def reference(kind: str, bucket: str, **extra) -> dict:
    return {
        "uri": f"s3://{bucket}/k",
        "sha256": HASH,
        "size": 1,
        "name": "n",
        "kind": kind,
        **extra,
    }


def test_kinds_come_from_the_layout():
    content, attempt = reference_models(extended())
    content.model_validate(reference("sbom", "wdf-sources"))
    attempt.model_validate(reference("trace", "wdf-traces", producer=PRODUCER))
    for model, bad in ((ContentReference, "sbom"), (AttemptReference, "trace")):
        with pytest.raises(ValidationError):
            model.model_validate(reference(bad, "wdf-traces", producer=PRODUCER))


def test_the_models_name_every_kind_of_the_layout_once():
    kinds = layout()["kinds"]
    named = [*get_args(ContentReference.model_fields["kind"].annotation)]
    named += get_args(AttemptReference.model_fields["kind"].annotation)
    assert sorted(named) == sorted(kinds)
    for model, klass in (
        (ContentReference, "content-addressed"),
        (AttemptReference, "attempt-scoped"),
    ):
        assert {k for k in get_args(model.model_fields["kind"].annotation)} == {
            k for k, v in kinds.items() if v["class"] == klass
        }


@pytest.mark.parametrize(
    ("narrowing", "klass"),
    [
        (ManifestReference, "content-addressed"),
        (CheckpointReference, "attempt-scoped"),
        (ResultReference, "attempt-scoped"),
    ],
)
def test_narrowings_name_existing_kinds(narrowing, klass):
    kinds = layout()["kinds"]
    names = get_args(narrowing.model_fields["kind"].annotation)
    assert names
    for name in names:
        assert kinds[name]["class"] == klass
    # A narrowing that pins its bucket pins the bucket of each kind it names.
    pinned = re.match(r"^\^s3://(wdf-[a-z]+)/", narrowing.model_fields["uri"].metadata[0].pattern)
    if pinned:
        assert {kinds[n]["bucket"] for n in names} == {pinned[1]}


def schema_with(doc: dict) -> dict:
    content, attempt = reference_models(doc)

    class Data(TaskRequestData):
        inputs: list[Annotated[content | attempt, Field(discriminator="kind")]]

    return Data.model_json_schema()


def test_a_new_kind_widens_the_task_request_compatibly():
    """A kind addition loosens the event schema, whose committed file must then be regenerated."""
    committed = json.loads((ROOT / "contracts/events/schemas/task.requested/1.0.json").read_text())
    assert breaking_changes(committed, schema_with(layout())) == []
    widened = schema_with(extended())
    assert breaking_changes(committed, widened) == []
    assert widened["$defs"] != committed["$defs"]


def flat_schema() -> dict:
    return json.loads(render_reference())


def test_a_new_kind_widens_the_reference_schema_and_a_removed_one_narrows_it():
    committed = json.loads((ROOT / "contracts/artifact/reference.schema.json").read_text())
    assert committed == flat_schema()
    grown = copy.deepcopy(committed)
    grown["properties"]["kind"]["enum"].append("sbom")
    assert breaking_changes(committed, grown) == []
    shrunk = copy.deepcopy(committed)
    shrunk["properties"]["kind"]["enum"].remove("source")
    assert breaking_changes(committed, shrunk) == [
        "data.kind: enum narrowed, 'source' no longer accepted"
    ]


def test_the_attempt_kinds_of_the_schema_are_the_scoped_kinds_of_the_layout():
    schema = flat_schema()
    scoped = {k for k, v in layout()["kinds"].items() if v["class"] == "attempt-scoped"}
    assert set(schema["if"]["properties"]["kind"]["enum"]) == scoped
    assert set(schema["properties"]["kind"]["enum"]) == set(layout()["kinds"])
