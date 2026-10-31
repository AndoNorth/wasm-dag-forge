"""The patch check vectors: a stub until its lane lands."""

import pytest
from wdf_jobpatch import patch_check

STUBBED = {"patch_check": patch_check}


@pytest.mark.parametrize("name", sorted(STUBBED))
def test_vectors_stub_error_names_its_lane(name):
    with pytest.raises(NotImplementedError, match="not implemented: owned by lane H"):
        STUBBED[name](None, None, None)
