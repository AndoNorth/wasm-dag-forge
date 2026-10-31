"""The key vectors against the functions of wdf_keys.

A name in STUBBED is still a stub and must raise the not-implemented error, and every other
name must match its vectors, so the stub and the real functions share this one test.
"""

import builtins
import json
from pathlib import Path

import pytest
import wdf_keys

VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "contracts/events/vectors/keys.json").read_text()
)
STUBBED = {
    "attempt_identity",
    "batch_id",
    "cloudevent_id",
    "compile_cache_key",
    "job_id",
    "workflow_id",
}
CASES = [(name, case) for name, cases in VECTORS["functions"].items() for case in cases]


def test_every_function_has_vectors():
    assert set(VECTORS["functions"]) == set(wdf_keys.__all__)
    assert STUBBED <= set(wdf_keys.__all__)


@pytest.mark.parametrize(
    ("name", "case"), CASES, ids=[f"{n}-{i}" for i, (n, _) in enumerate(CASES)]
)
def test_vector(name, case):
    function = getattr(wdf_keys, name)
    if name in STUBBED:
        with pytest.raises(NotImplementedError, match="not implemented: owned by lane K"):
            function(*case["args"])
    elif "raises" in case:
        with pytest.raises(getattr(builtins, case["raises"])):
            function(*case["args"])
    else:
        assert function(*case["args"]) == case["expect"]


@pytest.mark.parametrize("name", sorted(VECTORS["functions"]))
def test_boundary_pairs_differ_only_in_where_the_boundary_falls(name):
    groups = {}
    for case in VECTORS["functions"][name]:
        if "group" in case:
            groups.setdefault(case["group"], []).append(case)
    for group, cases in groups.items():
        assert len(cases) == 2, group
        first, second = cases
        assert first["args"] != second["args"], group
        assert "".join(map(str, first["args"])) == "".join(map(str, second["args"])), group
        assert first["expect"] != second["expect"], group
