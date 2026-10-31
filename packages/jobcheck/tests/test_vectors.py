"""Every invalid schema vector is refused through validate, with the code of its directory."""

from pathlib import Path

import pytest
from wdf_jobcheck import validate
from wdf_jobdoc.profile_files import load_profile

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "contracts/job/vectors/invalid"
PROFILE = load_profile("ci", ROOT)

# Vector classes whose pass is still a stub, by class directory name.
STUBBED = {"graph", "reference", "bundle"}

FILES = sorted(VECTORS.glob("*/*/*.json"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors(path):
    klass = path.parent.parent.name
    if klass in STUBBED:
        with pytest.raises(NotImplementedError, match="not implemented: owned by lane"):
            validate(path.read_bytes(), PROFILE)
        return
    report = validate(path.read_bytes(), PROFILE)
    assert not report.ok
    assert report.issues[0].code == path.parent.name
