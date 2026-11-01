import copy
import json

import pytest
from wdf_jobdoc.schemagen import DOCUMENT_MODELS, SCHEMA_DIR, render_document_schema
from wdf_plan.evolution import violations

SCHEMAS = sorted(DOCUMENT_MODELS)


def committed(root, name, version):
    return json.loads((root / SCHEMA_DIR / name / f"{version}.json").read_text())


@pytest.mark.parametrize(("name", "version"), SCHEMAS)
def test_evolution_committed_schema_is_additive_over_the_models(root, name, version):
    regenerated = json.loads(render_document_schema(name, version))
    assert violations(committed(root, name, version), regenerated, f"{name}/{version}") == []


@pytest.mark.parametrize(("name", "version"), SCHEMAS)
def test_evolution_every_model_is_reached_by_the_walk(root, name, version):
    """A required field added to any model of a schema is reported with its name."""
    old = committed(root, name, version)
    for model, definition in {"": old, **old.get("$defs", {})}.items():
        grown = copy.deepcopy(old)
        target = grown if not model else grown["$defs"][model]
        if "properties" not in definition:
            continue
        target["properties"]["added_field"] = {"type": "integer"}
        target["required"] = [*target.get("required", []), "added_field"]
        found = violations(old, grown)
        assert [m for m in found if m.endswith(".added_field: new required field")], model


def test_evolution_names_a_field_added_to_a_task_model(root):
    old = committed(root, "plan", "1")
    grown = copy.deepcopy(old)
    task = grown["$defs"]["SimulatePlanTask"]
    task["properties"]["priority"] = {"type": "integer"}
    task["required"].append("priority")
    assert violations(old, grown) == [
        "schema.tasks[]{SimulatePlanTask}.priority: new required field"
    ]


def test_evolution_consecutive_minors_of_a_document_are_additive(root):
    files = sorted((root / SCHEMA_DIR / "job").glob("*.json"))
    versions = [p.stem for p in files]
    for before, after in zip(versions, versions[1:], strict=False):
        if before.split(".")[0] == after.split(".")[0]:
            old, new = (committed(root, "job", v) for v in (before, after))
            assert violations(old, new, f"job/{after}") == []
