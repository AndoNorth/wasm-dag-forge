import copy

import pytest


def tasks_of(plan):
    return {t.id: t for t in plan.tasks}


def test_compile_defaults(good, plan_of, profile_of):
    compile_slot = profile_of("ci").pools.compile
    build = tasks_of(plan_of(good))["build"]
    assert (build.cpu_milli, build.memory_mib) == (compile_slot.cpu_milli, compile_slot.memory_mib)
    assert (build.run_timeout_s, build.retries) == (600, 1)
    assert (build.checkpoint, build.allow_partial) == (False, False)


def test_permissive_keeps_compile_defaults(good, plan_of):
    under_ci = tasks_of(plan_of(good, "ci"))["build"]
    under_permissive = tasks_of(plan_of(good, "permissive"))["build"]
    for field in ("cpu_milli", "memory_mib", "run_timeout_s", "retries"):
        assert getattr(under_permissive, field) == getattr(under_ci, field)


def test_the_plan_records_its_profile_and_the_default_parallelism(good, plan_of):
    plan = plan_of(good, "permissive")
    assert (plan.profile, plan.max_parallel) == ("permissive", 8)
    good["max_parallel"] = 3
    assert plan_of(good).max_parallel == 3


def test_resources_become_integer_millicores(good, plan_of):
    good["tasks"]["sim"]["cpu"] = 0.125
    good["tasks"]["agg"]["cpu"] = 2
    plan = tasks_of(plan_of(good))
    assert (plan["sim"].cpu_milli, plan["agg"].cpu_milli) == (125, 2000)


def test_edges_are_the_sorted_union_of_dependencies_and_inputs(good, plan_of):
    good["tasks"]["agg"]["depends_on"] = ["build", "sim"]
    plan = tasks_of(plan_of(good))
    assert plan["agg"].depends_on == ["build", "sim"]
    assert plan["sim"].depends_on == ["build"]
    assert plan["build"].depends_on == []


def test_pools_follow_the_task_type(good, plan_of):
    assert {t.id: t.pool for t in plan_of(good).tasks} == {
        "build": "compile",
        "sim": "simulate",
        "agg": "aggregate",
    }


def test_optional_aggregate_fields_are_normalised(good, plan_of):
    spelled = copy.deepcopy(good)
    spelled["tasks"]["agg"]["params"]["args"] = {}
    good["tasks"]["agg"]["params"].pop("args")
    assert plan_of(spelled) == plan_of(good)


def test_the_parameter_declarations_are_carried(good, plan_of):
    good["parameters"] = {"seed": "seed", "n": "integer"}
    assert plan_of(good).parameters == {"seed": "seed", "n": "integer"}


def test_every_valid_document_compiles(valid_documents, plan_of):
    for name, data in valid_documents.items():
        assert plan_of(data).tasks, name


def test_a_cycle_is_refused_rather_than_looped_on(good, plan_of):
    good["tasks"]["build"]["depends_on"] = ["agg"]
    with pytest.raises(ValueError, match="cycle"):
        plan_of(good)


def test_an_unknown_dependency_is_refused(good, plan_of):
    good["tasks"]["sim"]["depends_on"] = ["nowhere"]
    with pytest.raises(ValueError, match="nowhere"):
        plan_of(good)
