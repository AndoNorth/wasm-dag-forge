import copy
from pathlib import Path

import pytest
from wdf_jobpatch import PatchReport, patch_check
from wdf_plan import canonical_bytes

VECTORS = Path(__file__).resolve().parents[3] / "contracts/job/vectors/patch"
CASES = sorted(p for p in VECTORS.glob("*/*") if p.is_dir())


@pytest.mark.parametrize("case", CASES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_patch_check_is_pure(case, load_case):
    old, new, completed, _ = load_case(case)
    snapshot = (canonical_bytes(old), canonical_bytes(new), copy.deepcopy(completed))
    first = patch_check(old, new, completed)
    assert patch_check(old, new, completed) == first
    assert patch_check(old, new, frozenset(completed)) == first
    assert isinstance(first, PatchReport)
    assert (canonical_bytes(old), canonical_bytes(new), completed) == snapshot
