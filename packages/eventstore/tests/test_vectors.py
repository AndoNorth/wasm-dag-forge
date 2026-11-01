"""The stubs raise the lane error for exactly the names of STUBBED."""

import inspect

import pytest
import wdf_eventstore
from wdf_eventstore import stubs

FUNCTIONS = [
    name for name, f in inspect.getmembers(stubs, inspect.isfunction) if not name.startswith("_")
]


def test_every_stubbed_function_is_listed_and_every_listed_function_raises():
    assert sorted(FUNCTIONS) == sorted(n for n in stubs.STUBBED if n in FUNCTIONS)


@pytest.mark.parametrize("name", FUNCTIONS)
def test_a_stub_raises_the_lane_error(name):
    with pytest.raises(NotImplementedError, match="^not implemented: owned by lane E$"):
        getattr(stubs, name)(*[None] * len(inspect.signature(getattr(stubs, name)).parameters))


def test_the_package_exports_each_stub():
    assert all(hasattr(wdf_eventstore, name) for name in FUNCTIONS)
