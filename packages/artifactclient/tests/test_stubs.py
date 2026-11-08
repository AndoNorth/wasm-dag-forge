"""A stub raises the lane error for exactly the names of its module's STUBBED, and nothing else."""

import importlib
import inspect

import pytest
from wdf_artifactclient.contract import failure_codes

LANES = {
    "client": "C",
    "errors": "C",
    "keys": "C",
    "commit": "C",
    "staging": "C",
    "__main__": "C",
    "checkpoint": "K",
    "aggregate": "K",
    "cache": "K",
    "bench": "K",
    "faults": "F",
}
MODULES = {name: importlib.import_module(f"wdf_artifactclient.{name}") for name in LANES}


@pytest.mark.parametrize("module", MODULES)
def test_every_stub_of_a_module_is_listed(module):
    """A public name defined in a module that raises the lane error must be in STUBBED."""
    stubbed = MODULES[module].STUBBED
    for name, obj in inspect.getmembers(MODULES[module]):
        if name.startswith("_") or getattr(obj, "__module__", None) != MODULES[module].__name__:
            continue
        if inspect.isfunction(obj) or inspect.isclass(obj):
            inherited = inspect.isclass(obj) and any(b.__name__ in stubbed for b in obj.__mro__[1:])
            raises = "not_implemented(" in inspect.getsource(obj) or inherited
            assert raises == (name in stubbed), name


def test_the_typed_errors_are_the_exceptions_and_codes_of_failures_yaml():
    from wdf_artifactclient import errors

    classes = {n: c for n, c in inspect.getmembers(errors, inspect.isclass) if n != "ArtifactError"}
    classes = {n: c for n, c in classes.items() if issubclass(c, errors.ArtifactError)}
    assert {c.code for c in classes.values()} == failure_codes()
