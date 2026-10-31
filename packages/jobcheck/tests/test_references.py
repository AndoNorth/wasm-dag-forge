"""The reference pass: registry rules for inputs, the aggregate-only form and placeholders."""

from pathlib import Path

import pytest
from wdf_jobcheck.references import check_references
from wdf_jobdoc import parse_document

VECTORS = Path(__file__).resolve().parents[3] / "contracts/job/vectors"
FILES = sorted((VECTORS / "invalid/reference").glob("*/*.json"))


def found(parse, raw):
    return [(i.code, i.path) for i in check_references(parse(raw))]


def test_a_valid_document_has_no_reference_issue(parse, good):
    assert found(parse, good) == []


def test_a_lone_aggregate_over_an_index_is_valid(parse, good):
    agg = good["tasks"]["agg"]
    index = {**good["tasks"]["build"]["params"]["source"], "kind": "index"}
    agg["inputs"] = []
    agg["params"]["index"] = index
    good["tasks"] = {"agg": agg}
    assert found(parse, good) == []


def test_a_compile_task_that_consumes_a_simulate_output_is_refused(parse, good):
    good["tasks"]["build"]["inputs"] = [{"task": "sim", "output": "result"}]
    codes = [c for c, _ in found(parse, good)]
    assert codes[0] == "input-kind-not-accepted"


def test_a_simulate_task_with_no_component_input_is_refused(parse, good):
    del good["tasks"]["sim"]["inputs"]
    assert found(parse, good) == [("missing-required-input", "/tasks/sim")]


def test_a_bad_input_is_not_also_reported_as_a_missing_one(parse, good):
    good["tasks"]["sim"]["inputs"] = [{"task": "build", "output": "build-info"}]
    assert [c for c, _ in found(parse, good)] == ["input-kind-not-accepted"]


def test_the_pointer_of_an_unknown_producer_is_the_task_field(parse, good):
    good["tasks"]["sim"]["inputs"] = [{"task": "ghost", "output": "component"}]
    assert ("unknown-producer", "/tasks/sim/inputs/0/task") in found(parse, good)


def test_an_undeclared_output_points_at_the_output_field(parse, good):
    good["tasks"]["sim"]["inputs"] = [{"task": "build", "output": "wasm"}]
    assert ("undeclared-output", "/tasks/sim/inputs/0/output") in found(parse, good)


def test_a_repeated_input_points_at_the_second_entry(parse, good):
    good["tasks"]["agg"]["inputs"] = [{"task": "sim", "output": "result"}] * 2
    assert found(parse, good) == [("duplicate-input", "/tasks/agg/inputs/1")]


def test_an_aggregate_reads_any_number_of_results(parse, good):
    good["tasks"]["sim2"] = good["tasks"]["sim"]
    good["tasks"]["agg"]["inputs"].append({"task": "sim2", "output": "result"})
    assert found(parse, good) == []


def test_a_placeholder_for_a_simulate_argument_may_have_any_declared_type(parse, good):
    good["parameters"].update(count="integer", label="string", flag="boolean", names="string-list")
    args = good["tasks"]["sim"]["params"]["args"]
    args.update(
        a={"param": "count"}, b={"param": "label"}, c={"param": "flag"}, d={"param": "names"}
    )
    assert found(parse, good) == []


def test_a_string_list_placeholder_fits_both_aggregate_arguments(parse, good):
    good["parameters"]["names"] = "string-list"
    args = good["tasks"]["agg"]["params"]["args"]
    args.update(blocks={"param": "names"}, columns={"param": "names"})
    assert found(parse, good) == []


def test_a_placeholder_in_a_digest_list_has_no_position(parse, good):
    agg = good["tasks"]["agg"]
    agg["params"] = {"aggregation": "digest_list"}
    assert found(parse, good) == []


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors_first_issue_has_the_code_of_its_directory(path):
    document = parse_document(path.read_bytes(), 1 << 20)
    assert check_references(document)[0].code == path.parent.name
