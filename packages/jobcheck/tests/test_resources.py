"""The resource pass and pool_budget."""

from pathlib import Path

import pytest
import yaml
from wdf_jobcheck import pool_budget
from wdf_jobcheck.resources import check_resources
from wdf_jobdoc import parse_document
from wdf_jobdoc.profile import parse_cpu_milli, parse_memory_mib
from wdf_jobdoc.profile_files import load_profile

ROOT = Path(__file__).resolve().parents[3]
FILES = sorted((ROOT / "contracts/job/vectors/invalid/resource").glob("*/*.json"))


def found(parse, raw, profile):
    return [(i.code, i.path) for i in check_resources(parse(raw), profile)]


@pytest.mark.parametrize("task_type", ["compile", "simulate", "aggregate"])
def test_pool_budget_matches_slots(task_type):
    slots = yaml.safe_load((ROOT / "deploy/presets/slots/ci.yaml").read_text())
    limits = slots["pools"][task_type]["limits"]
    budget = pool_budget(load_profile("ci", ROOT), task_type)
    assert budget.cpu_milli == parse_cpu_milli(limits["cpu"])
    assert budget.memory_mib == parse_memory_mib(limits["memory"])


def test_a_valid_document_fits_its_slots(parse, good, profiles):
    assert found(parse, good, profiles["ci"]) == []


def test_a_task_exactly_at_its_budget_fits(parse, good, profiles):
    slot = pool_budget(profiles["ci"], "simulate")
    good["tasks"]["sim"]["cpu"] = slot.cpu_milli / 1000
    good["tasks"]["sim"]["memory_mib"] = slot.memory_mib
    assert found(parse, good, profiles["ci"]) == []


def test_a_task_one_unit_above_its_budget_is_refused_at_the_field(parse, good, profiles):
    slot = pool_budget(profiles["ci"], "simulate")
    good["tasks"]["sim"]["cpu"] = (slot.cpu_milli + 1) / 1000
    good["tasks"]["sim"]["memory_mib"] = slot.memory_mib + 1
    assert found(parse, good, profiles["ci"]) == [
        ("cpu-exceeds-slot", "/tasks/sim/cpu"),
        ("memory-exceeds-slot", "/tasks/sim/memory_mib"),
    ]


def test_compile_tasks_are_never_refused_for_resources(parse, good, profiles):
    assert not any(p.startswith("/tasks/build") for _, p in found(parse, good, profiles["ci"]))


@pytest.mark.parametrize("path", FILES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vectors_fail_under_ci_and_pass_under_permissive(path, profiles):
    document = parse_document(path.read_bytes(), 1 << 20)
    assert check_resources(document, profiles["ci"])[0].code == path.parent.name
    assert check_resources(document, profiles["permissive"]) == ()


def test_the_permissive_budget_is_its_ceiling_for_every_pool(profiles):
    for task_type in ("compile", "simulate", "aggregate"):
        budget = pool_budget(profiles["permissive"], task_type)
        assert (budget.cpu_milli, budget.memory_mib) == (1_000_000, 1_048_576)
