"""Properties of the graph pass over generated task graphs."""

import json

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from wdf_jobcheck.graph import check_graph
from wdf_jobdoc import parse_document

SOURCE = "a" * 64
BUILD = {
    "type": "compile",
    "params": {
        "source": {
            "uri": f"s3://wdf-sources/sha256/aa/{SOURCE}",
            "sha256": SOURCE,
            "size": 1,
            "name": "b.tar.gz",
            "kind": "source",
        },
        "entry": "sim",
    },
}
SIM = {
    "type": "simulate",
    "cpu": 1,
    "memory_mib": 1,
    "run_timeout_s": 1,
    "retries": 0,
    "params": {"workload": "w", "seed": 1},
}


def document(deps: dict[int, list[int]]):
    """A document whose task `t<i>` depends on `t<j>` for each j in deps[i]."""
    tasks = {"build": BUILD}
    for i, ds in deps.items():
        tasks[f"t{i}"] = {**SIM, "depends_on": [f"t{j}" for j in ds]}
    raw = {"schema": "urn:wdf:schema:job:1.0", "tasks": tasks}
    return parse_document(json.dumps(raw).encode(), 1 << 20)


@st.composite
def acyclic(draw):
    """Edges only point from a higher index to a lower one, so no cycle exists."""
    size = draw(st.integers(1, 12))
    return {i: sorted(draw(st.sets(st.integers(0, i - 1)))) if i else [] for i in range(size)}


@given(acyclic())
@settings(max_examples=100)
def test_acyclic_documents_are_accepted(deps):
    assert check_graph(document(deps)) == ()


@given(acyclic(), st.data())
@settings(max_examples=100)
def test_cycle_path_is_a_real_cycle(deps, data):
    """A back edge from the end of a walk of real edges to its start always makes a cycle."""
    high = data.draw(st.integers(0, len(deps) - 1))
    low = high
    while deps[low] and data.draw(st.booleans()):
        low = data.draw(st.sampled_from(deps[low]))
    assume(low != high)
    deps[low] = sorted({*deps[low], high})
    cycles = [i for i in check_graph(document(deps)) if i.code == "dependency-cycle"]
    assert cycles
    for issue in cycles:
        walk = issue.message.split(": ", 1)[1].split(" -> ")
        assert walk[0] == walk[-1]
        for here, there in zip(walk, walk[1:], strict=False):
            assert int(there[1:]) in deps[int(here[1:])]
