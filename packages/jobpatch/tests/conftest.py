import json
from pathlib import Path

import pytest
from wdf_jobdoc import parse_document
from wdf_jobdoc.profile_files import load_profile
from wdf_plan import bind, compile_plan

ROOT = Path(__file__).resolve().parents[3]
SLOTS_FIXTURE = ROOT / "contracts/job/vectors/slots-fixture"


@pytest.fixture(scope="session")
def profile():
    """The ci limits over the slots fixture, so a live slots edit changes no verdict."""
    return load_profile("ci", ROOT, SLOTS_FIXTURE)


@pytest.fixture(scope="session")
def bound_plan(profile):
    """The plan of a document bound to a job's values, as the patch command prepares each side."""

    def build(document: bytes, params: dict):
        template = compile_plan(parse_document(document, profile.max_document_bytes), profile)
        return bind(template, params, profile)

    return build


@pytest.fixture(scope="session")
def load_case(bound_plan):
    """`(old, new, completed, expected)` of one case directory."""

    def load(directory: Path):
        def read(name):
            return json.loads((directory / name).read_text())

        old_params = read("params.json")
        new_file = directory / "new-params.json"
        new_params = read("new-params.json") if new_file.exists() else old_params
        old = bound_plan((directory / "old.json").read_bytes(), old_params)
        new = bound_plan((directory / "new.json").read_bytes(), new_params)
        return old, new, set(read("completed.json")), read("expected.json")

    return load
