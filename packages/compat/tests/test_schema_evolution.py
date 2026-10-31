"""A minor may add an optional field, and a rename or a new required field needs a new major."""

import json
from pathlib import Path

import pytest
from wdf_compat import breaking_changes
from wdf_events.models import DATA_MODELS
from wdf_events.profile import CONTRACT_DIR, known_versions
from wdf_events.schemagen import render

FIXTURES = Path(__file__).parent / "fixtures/evolution"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def test_an_added_optional_field_is_compatible():
    assert breaking_changes(load("base"), load("optional-added")) == []


def test_a_renamed_field_needs_a_new_major():
    assert breaking_changes(load("base"), load("renamed")) == [
        "data.name: field removed or renamed",
        "data.title: new required field",
    ]


def test_a_new_required_field_needs_a_new_major():
    assert breaking_changes(load("base"), load("required-added")) == [
        "data.note: new required field"
    ]


def test_a_narrowed_constraint_needs_a_new_major():
    assert breaking_changes(load("base"), load("narrowed")) == ["data.name: maxLength tightened"]


def test_a_narrowed_enum_and_type_need_a_new_major():
    wide = {"properties": {"kind": {"enum": ["a", "b"]}, "n": {"type": ["integer", "string"]}}}
    narrow = {"properties": {"kind": {"enum": ["a"]}, "n": {"type": "integer"}}}
    assert breaking_changes(wide, narrow) == [
        "data.kind: enum narrowed, 'b' no longer accepted",
        "data.n: type narrowed, string no longer accepted",
    ]


def test_a_removed_nested_field_is_found_through_a_reference():
    old = {
        "$defs": {"Ref": {"properties": {"uri": {"type": "string"}}}},
        "properties": {"m": {"$ref": "#/$defs/Ref"}},
    }
    new = {"$defs": {"Ref": {"properties": {}}}, "properties": {"m": {"$ref": "#/$defs/Ref"}}}
    assert breaking_changes(old, new) == ["data.m.uri: field removed or renamed"]


def committed(name: str, major: int, minor: int) -> dict:
    return json.loads((CONTRACT_DIR / f"schemas/{name}/{major}.{minor}.json").read_text())


@pytest.mark.parametrize("key", sorted(DATA_MODELS), ids=lambda key: f"{key[0]}-{key[1]}")
def test_a_model_changes_nothing_that_needs_a_new_major(key):
    name, version = key
    assert (
        breaking_changes(committed(name, *map(int, version.split("."))), json.loads(render(*key)))
        == []
    )


@pytest.mark.parametrize("name", sorted({name for name, _ in DATA_MODELS}))
def test_the_minors_of_a_major_only_add_optional_fields(name):
    versions = known_versions(name)
    for (major, minor), (next_major, next_minor) in zip(versions, versions[1:], strict=False):
        if major == next_major:
            assert (
                breaking_changes(
                    committed(name, major, minor), committed(name, next_major, next_minor)
                )
                == []
            )
