import pytest
from wdf_plan.evolution import violations

MODEL = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
GROWN = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
}


def pair(shape):
    """An old and a new schema that hold MODEL and GROWN under one shape of nesting."""

    def build(model):
        defs = {"Model": model}
        ref = {"$ref": "#/$defs/Model"}
        root = {
            "map": {"type": "object", "additionalProperties": ref},
            "pattern": {"type": "object", "patternProperties": {"^[a-z]+$": ref}},
            "union": {"type": "array", "items": {"oneOf": [ref, {"type": "null"}]}},
            "any": {"anyOf": [ref, {"type": "null"}]},
            "items": {"type": "array", "items": ref},
            "field": {"type": "object", "properties": {"inner": ref}},
        }[shape]
        return {"$defs": defs} | root

    return build(MODEL), build(GROWN)


@pytest.mark.parametrize(
    ("shape", "path"),
    [
        ("map", "schema[*]{Model}.b"),
        ("pattern", "schema[*]{Model}.b"),
        ("union", "schema[]{Model}.b"),
        ("any", "schema{Model}.b"),
        ("items", "schema[]{Model}.b"),
        ("field", "schema.inner{Model}.b"),
    ],
)
def test_walker_descends(shape, path):
    assert violations(*pair(shape)) == [f"{path}: new required field"]


def test_walker_reports_a_removed_field():
    removed = {"type": "object", "properties": {}, "required": []}
    old, new = pair("map")
    new["$defs"]["Model"] = removed
    assert violations(old, new) == ["schema[*]{Model}.a: field removed or renamed"]


def test_walker_reports_a_removed_variant():
    old, _ = pair("union")
    new = {"$defs": old["$defs"], "type": "array", "items": {"oneOf": [{"type": "null"}]}}
    assert violations(old, new) == ["schema[]: variant #/$defs/Model removed"]


def test_walker_allows_an_optional_field():
    optional = GROWN | {"required": ["a"]}
    old, new = pair("map")
    new["$defs"]["Model"] = optional
    assert violations(old, new) == []


def test_walker_ends_on_a_model_that_refers_to_itself():
    node = {"properties": {"child": {"$ref": "#/$defs/Node"}}, "required": []}
    schema = {"$defs": {"Node": node}, "$ref": "#/$defs/Node"}
    assert violations(schema, schema) == []
