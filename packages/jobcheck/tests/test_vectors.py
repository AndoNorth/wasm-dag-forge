"""Every invalid vector is refused through validate, with the code of its directory first."""

import json
from pathlib import Path

import pytest
from wdf_jobcheck import StaticResolver, validate
from wdf_jobdoc.profile_files import load_profile

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "contracts/job/vectors/invalid"
PROFILE = load_profile("ci", ROOT)
RESOLVER = StaticResolver(json.loads((ROOT / "contracts/job/vectors/resolver.json").read_text()))

FILES = sorted(VECTORS.glob("*/*/*.json"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors(path):
    report = validate(path.read_bytes(), PROFILE, RESOLVER)
    assert not report.ok
    assert report.issues[0].code == path.parent.name
