"""The graph pass: dependency faults and cycles, by example and over the invalid vectors."""

from pathlib import Path

import pytest
from wdf_jobcheck.graph import check_graph
from wdf_jobdoc import parse_document

VECTORS = Path(__file__).resolve().parents[3] / "contracts/job/vectors"
FILES = sorted((VECTORS / "invalid/graph").glob("*/*.json"))


def found(parse, raw):
    return [(i.code, i.path) for i in check_graph(parse(raw))]


def test_a_valid_document_has_no_graph_issue(parse, good):
    assert found(parse, good) == []


def test_a_missing_task_in_depends_on_points_at_the_entry(parse, good):
    good["tasks"]["agg"]["depends_on"] = ["sim", "ghost"]
    assert found(parse, good) == [("unknown-dependency", "/tasks/agg/depends_on/1")]


def test_a_task_listing_itself_is_a_self_dependency(parse, good):
    good["tasks"]["agg"]["depends_on"] = ["agg"]
    assert found(parse, good) == [("self-dependency", "/tasks/agg/depends_on/0")]


def test_an_input_from_itself_is_a_self_dependency_and_no_cycle(parse, good):
    good["tasks"]["agg"]["inputs"].append({"task": "agg", "output": "summary"})
    assert found(parse, good) == [("self-dependency", "/tasks/agg/inputs/1/task")]


def test_an_input_from_a_missing_task_is_left_to_the_reference_pass(parse, good):
    good["tasks"]["agg"]["inputs"].append({"task": "ghost", "output": "result"})
    assert found(parse, good) == []


def test_a_three_task_cycle_lists_the_tasks_and_repeats_the_first(parse, good):
    for name, dep in (("a", "c"), ("b", "a"), ("c", "b")):
        good["tasks"][name] = {**good["tasks"]["sim"], "depends_on": [dep]}
    (issue,) = check_graph(parse(good))
    assert issue.code == "dependency-cycle"
    assert issue.path == "/tasks/a"
    assert issue.message.endswith("a -> c -> b -> a")


def test_an_input_edge_closes_a_cycle_with_depends_on(parse, good):
    good["tasks"]["sim"]["depends_on"] = ["agg"]
    (issue,) = check_graph(parse(good))
    assert issue.code == "dependency-cycle"
    assert issue.message.endswith("agg -> sim -> agg") or issue.message.endswith(
        "sim -> agg -> sim"
    )


def test_two_separate_cycles_are_both_reported(parse, good):
    for name, dep in (("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")):
        good["tasks"][name] = {**good["tasks"]["sim"], "depends_on": [dep]}
    paths = [i.path for i in check_graph(parse(good)) if i.code == "dependency-cycle"]
    assert paths == ["/tasks/a", "/tasks/c"]


def test_a_cycle_on_the_way_in_does_not_hide_the_tasks_that_lead_to_it(parse, good):
    good["tasks"]["a"] = {**good["tasks"]["sim"], "depends_on": ["b"]}
    good["tasks"]["b"] = {**good["tasks"]["sim"], "depends_on": ["a"]}
    good["tasks"]["agg"]["depends_on"] = ["a"]
    cycles = [i for i in check_graph(parse(good)) if i.code == "dependency-cycle"]
    assert len(cycles) == 1


def test_a_long_chain_does_not_overflow_the_stack(parse, good):
    tasks = good["tasks"]
    for i in range(3000):
        tasks[f"t{i}"] = {**tasks["sim"], "depends_on": [f"t{i + 1}"] if i < 2999 else ["t0"]}
    (issue,) = check_graph(parse(good))
    assert issue.code == "dependency-cycle"


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors_first_issue_has_the_code_of_its_directory(path):
    document = parse_document(path.read_bytes(), 1 << 20)
    assert check_graph(document)[0].code == path.parent.name
