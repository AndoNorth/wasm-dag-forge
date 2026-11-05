"""A stub raises the lane error for exactly the names of its module's STUBBED, and nothing else."""

import importlib
import inspect

import pytest

LANES = {"reap": "P", "schedule": "P", "__main__": "P"}
MODULES = {name: importlib.import_module(f"wdf_artifactreaper.{name}") for name in LANES}
STUBS = [(name, stub) for name, module in MODULES.items() for stub in module.STUBBED]


def required_arguments(obj) -> list[None]:
    parameters = inspect.signature(obj).parameters.values()
    return [
        None
        for p in parameters
        if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]


@pytest.mark.parametrize(("module", "name"), STUBS)
def test_a_stub_raises_only_the_error_of_its_lane(module, name):
    with pytest.raises(NotImplementedError) as raised:
        obj = getattr(MODULES[module], name)
        obj(*required_arguments(obj))
    assert str(raised.value) == f"not implemented: owned by lane {LANES[module]}"


@pytest.mark.parametrize("module", MODULES)
def test_every_stub_of_a_module_is_listed(module):
    """A public name defined in a module that raises the lane error must be in STUBBED."""
    stubbed = MODULES[module].STUBBED
    for name, obj in inspect.getmembers(MODULES[module]):
        if name.startswith("_") or getattr(obj, "__module__", None) != MODULES[module].__name__:
            continue
        if inspect.isfunction(obj) or inspect.isclass(obj):
            assert ("not_implemented(" in inspect.getsource(obj)) == (name in stubbed), name


def test_the_schedule_names_are_the_ones_of_the_breakdown():
    schedule = MODULES["schedule"]
    assert schedule.SCHEDULE_ID == "wdf-artifact-reaper"
    assert schedule.TASK_QUEUE
