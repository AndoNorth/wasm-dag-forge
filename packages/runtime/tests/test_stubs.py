"""A stub raises the lane error for exactly the names of its module's STUBBED, and nothing else."""

import importlib
import inspect

import pytest

LANES = {"command": "R", "classify": "R", "__main__": "R", "calibrate": "R"}
MODULES = {name: importlib.import_module(f"wdf_runtime.{name}") for name in LANES}
STUBS = [(module, name) for module in MODULES for name in MODULES[module].STUBBED]


@pytest.mark.parametrize("module", MODULES)
def test_every_stub_of_a_module_is_listed(module):
    """A public name defined in a module that raises the lane error must be in STUBBED."""
    stubbed = MODULES[module].STUBBED
    for name, obj in inspect.getmembers(MODULES[module]):
        if name.startswith("_") or getattr(obj, "__module__", None) != MODULES[module].__name__:
            continue
        if inspect.isfunction(obj) or inspect.isclass(obj):
            assert ("not_implemented(" in inspect.getsource(obj)) == (name in stubbed), name


@pytest.mark.parametrize("module,name", STUBS)
def test_a_listed_stub_raises_the_error_of_its_lane(module, name):
    stub = getattr(MODULES[module], name)
    required = [p for p in inspect.signature(stub).parameters.values() if p.default is p.empty]
    expected = f"^not implemented: owned by lane {LANES[module]}$"
    with pytest.raises(NotImplementedError, match=expected):
        stub(*[None] * len(required))
