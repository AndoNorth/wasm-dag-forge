"""The limits pass: each limit refused one past its value, accepted exactly at it, and by vector."""

import copy
from pathlib import Path

import pytest
from wdf_jobcheck.limits import check_limits
from wdf_jobdoc import parse_document
from wdf_plan import canonical_bytes, compile_plan, stats

ROOT = Path(__file__).resolve().parents[3]
FILES = sorted((ROOT / "contracts/job/vectors/invalid/limit").glob("*/*.json"))


def codes(parse, raw, profile):
    return [i.code for i in check_limits(parse(raw), profile)]


def many(good, count, retries=0):
    """The valid document with its simulate task repeated until `count` simulate tasks exist."""
    sim = good["tasks"]["sim"]
    del good["tasks"]["sim"], good["tasks"]["agg"]
    for i in range(count):
        good["tasks"][f"s{i:03d}"] = {**copy.deepcopy(sim), "retries": retries}
    return good


def test_a_valid_document_is_within_every_limit(parse, good, profiles):
    assert codes(parse, good, profiles["ci"]) == []


def test_512_tasks_are_accepted_and_513_are_not(parse, good, profiles):
    ci = profiles["ci"]
    assert codes(parse, many(copy.deepcopy(good), ci.max_tasks - 1), ci) == []
    assert codes(parse, many(copy.deepcopy(good), ci.max_tasks), ci) == ["too-many-tasks"]


def test_max_parallel_at_the_ceiling_is_accepted(parse, good, profiles):
    ci = profiles["ci"]
    good["max_parallel"] = ci.max_parallel
    assert codes(parse, good, ci) == []
    good["max_parallel"] = ci.max_parallel + 1
    assert codes(parse, good, ci) == ["max-parallel-exceeded"]


def test_a_run_timeout_at_the_ceiling_is_accepted(parse, good, profiles):
    ci = profiles["ci"]
    good["tasks"]["sim"]["run_timeout_s"] = ci.max_run_timeout_s
    good["tasks"]["sim"]["retries"] = 0
    assert codes(parse, good, ci) == []
    good["tasks"]["sim"]["run_timeout_s"] += 1
    assert codes(parse, good, ci) == ["run-timeout-too-long"]


def test_retries_at_the_ceiling_are_accepted(parse, good, profiles):
    ci = profiles["ci"]
    good["tasks"]["sim"]["retries"] = ci.max_retries
    assert codes(parse, good, ci) == []
    good["tasks"]["sim"]["retries"] += 1
    assert codes(parse, good, ci) == ["retries-too-many"]


def test_the_critical_path_exactly_at_the_limit_is_accepted(parse, good, profiles):
    ci = profiles["ci"]
    sim = good["tasks"]["sim"]
    sim["retries"] = 3
    # The path is the compile task, the simulate task and the aggregate task, each timeout times
    # one plus its retries, and the aggregate timeout absorbs the remainder so the sum is exact.
    compile_bound = ci.compile_run_timeout_s * (1 + ci.compile_retries)
    spare = ci.critical_path_limit_s - compile_bound - 1
    agg_timeout = 1 + spare % 4
    sim["run_timeout_s"] = spare // 4
    good["tasks"]["agg"]["run_timeout_s"] = agg_timeout
    good["tasks"]["agg"]["retries"] = 0
    assert compile_bound + sim["run_timeout_s"] * 4 + agg_timeout == ci.critical_path_limit_s
    assert codes(parse, good, ci) == []
    sim["run_timeout_s"] += 1
    assert codes(parse, good, ci) == ["critical-path-too-long"]


def test_history_events_exactly_at_the_limit_are_accepted(parse, good, profiles):
    ci = profiles["ci"]
    # With one compile task of one retry, events are 10 + 8T + 6R. 386 simulate tasks whose retries
    # sum to 1148 make T = 387 and R = 1149, which is exactly 10,000.
    document = many(copy.deepcopy(good), 386, retries=3)
    for i in range(10):
        document["tasks"][f"s{i:03d}"]["retries"] = 2
    exact = stats(compile_plan(parse(document), ci), ci).estimated_history_events
    assert exact == ci.max_history_events
    assert codes(parse, document, ci) == []
    document["tasks"]["s000"]["retries"] = 3
    assert codes(parse, document, ci) == ["history-budget-exceeded"]


def test_the_plan_exactly_at_the_size_limit_is_accepted(parse, good, profiles):
    ci = profiles["ci"]
    good["tasks"]["sim"]["params"]["args"]["pad"] = ""
    size = len(canonical_bytes(compile_plan(parse(good), ci)))
    good["tasks"]["sim"]["params"]["args"]["pad"] = "x" * (ci.max_plan_bytes - size)
    assert len(canonical_bytes(compile_plan(parse(good), ci))) == ci.max_plan_bytes
    assert codes(parse, good, ci) == []
    good["tasks"]["sim"]["params"]["args"]["pad"] += "x"
    assert codes(parse, good, ci) == ["plan-too-large"]


def test_a_document_with_a_cycle_is_left_to_the_graph_pass(parse, good, profiles):
    good["tasks"]["build"]["depends_on"] = ["agg"]
    assert codes(parse, good, profiles["ci"]) == []


def test_the_permissive_profile_lifts_the_limits(parse, good, profiles):
    document = many(copy.deepcopy(good), 513)
    document["max_parallel"] = 100
    assert codes(parse, document, profiles["permissive"]) == []


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors_first_issue_has_the_code_of_its_directory(path, profiles):
    document = parse_document(path.read_bytes(), 1 << 20)
    assert check_limits(document, profiles["ci"])[0].code == path.parent.name
