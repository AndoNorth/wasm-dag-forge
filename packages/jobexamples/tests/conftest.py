import json
from pathlib import Path

import pytest
from wdf_jobdoc import parse_document
from wdf_jobdoc.profile_files import load_profile

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "contracts/job"
VECTORS = CONTRACT / "vectors"


@pytest.fixture(scope="session")
def profile_of():
    """The limits profile `name` over the slots fixture, so a live slots edit changes no result."""
    return lambda name="ci": load_profile(name, ROOT, VECTORS / "slots-fixture")


@pytest.fixture(scope="session")
def parse(profile_of):
    return lambda data: parse_document(data, profile_of("permissive").max_document_bytes)


@pytest.fixture(scope="session")
def resolver():
    from wdf_jobcheck import StaticResolver

    return StaticResolver(json.loads((VECTORS / "resolver.json").read_text()))
