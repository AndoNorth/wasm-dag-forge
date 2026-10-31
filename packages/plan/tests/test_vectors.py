"""The plan library vectors: every one is a stub until its lane lands."""

import pytest
from wdf_plan import bind, compile_plan

# Names of the stubbed entry points, removed as each lane implements one.
STUBBED = {"compile_plan": (compile_plan, 2), "bind": (bind, 3)}


@pytest.mark.parametrize("name", sorted(STUBBED))
def test_vectors_stub_error_names_its_lane(name):
    stub, arity = STUBBED[name]
    with pytest.raises(NotImplementedError, match="not implemented: owned by lane P"):
        stub(*[None] * arity)
