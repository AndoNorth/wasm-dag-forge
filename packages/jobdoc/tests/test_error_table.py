"""The error table is valid, names the eight classes, and is what the README says it is."""

import json
import re

import pytest
import yaml
from jsonschema import ValidationError, validate

CLASSES = ["schema", "graph", "reference", "bundle", "resource", "limit", "params", "patch"]


def table(contract):
    return yaml.safe_load((contract / "errors.yaml").read_text())["codes"]


def test_errors_yaml_validates_against_its_schema(contract):
    schema = json.loads((contract / "errors.schema.json").read_text())
    validate(yaml.safe_load((contract / "errors.yaml").read_text()), schema)


def test_every_class_has_codes_and_no_code_has_another_class(contract):
    classes = {entry["class"] for entry in table(contract).values()}
    assert classes == set(CLASSES)


def test_the_readme_lists_every_class(contract):
    readme = (contract / "README.md").read_text()
    missing = [c for c in CLASSES if f"`{c}`" not in readme]
    assert not missing


def test_the_readme_does_not_copy_the_codes(contract):
    readme = (contract / "README.md").read_text()
    copied = [code for code in table(contract) if re.search(rf"`{re.escape(code)}`", readme)]
    assert not copied, "codes live in errors.yaml only"


def test_a_code_that_is_not_kebab_case_is_refused(contract):
    schema = json.loads((contract / "errors.schema.json").read_text())
    with pytest.raises(ValidationError, match="Not_Kebab"):
        validate({"codes": {"Not_Kebab": {"class": "schema", "meaning": "x"}}}, schema)


def test_an_unknown_class_is_refused(contract):
    schema = json.loads((contract / "errors.schema.json").read_text())
    with pytest.raises(ValidationError, match="other"):
        validate({"codes": {"a-code": {"class": "other", "meaning": "x"}}}, schema)
