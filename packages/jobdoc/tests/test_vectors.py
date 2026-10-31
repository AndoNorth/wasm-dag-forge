"""Every invalid schema vector is refused with the code of its directory, first."""

from pathlib import Path

import pytest
import yaml
from wdf_jobdoc import DocumentInvalid, parse_document
from wdf_jobdoc.profile_files import load_profile

CONTRACT = Path(__file__).resolve().parents[3] / "contracts/job"
VECTORS = CONTRACT / "vectors/invalid/schema"
ROOT = CONTRACT.parents[1]
CI_DOCUMENT_LIMIT = load_profile("ci", ROOT, CONTRACT / "vectors/slots-fixture").max_document_bytes

FILES = sorted(VECTORS.glob("*/*.json"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors_first_issue_has_the_code_of_its_directory(path):
    with pytest.raises(DocumentInvalid) as info:
        parse_document(path.read_bytes(), CI_DOCUMENT_LIMIT)
    assert info.value.issues[0].code == path.parent.name


def test_every_vector_directory_is_a_code_of_class_schema():
    codes = yaml.safe_load((CONTRACT / "errors.yaml").read_text())["codes"]
    schema = {code for code, entry in codes.items() if entry["class"] == "schema"}
    assert {p.parent.name for p in FILES} <= schema


def test_every_schema_code_has_a_vector():
    codes = yaml.safe_load((CONTRACT / "errors.yaml").read_text())["codes"]
    schema = {code for code, entry in codes.items() if entry["class"] == "schema"}
    assert schema <= {p.parent.name for p in FILES}


def test_the_size_vector_is_just_over_the_limit():
    (path,) = (VECTORS / "document-too-large").glob("*.json")
    assert path.stat().st_size == CI_DOCUMENT_LIMIT + 1
