import pytest
from wdf_plan import chains, stats

GOLDEN = {
    "chain": dict(
        task_count=3,
        counts=dict(compile=1, simulate=1, aggregate=1),
        chain_count=1,
        max_width=1,
        critical_path_timeout_bound_s=1200 + 1200 + 600,
        peak_cpu_milli=1000,
        peak_memory_mib=1024,
        largest_cpu_milli=1000,
        largest_memory_mib=1024,
        checkpoint_tasks=0,
        estimated_history_events=46,
    ),
    "fan-out": dict(
        task_count=9,
        counts=dict(compile=1, simulate=8, aggregate=0),
        chain_count=1,
        max_width=8,
        critical_path_timeout_bound_s=1200 + 100,
        peak_cpu_milli=2000,
        peak_memory_mib=8 * 384,
        largest_cpu_milli=1000,
        largest_memory_mib=1024,
        checkpoint_tasks=0,
        estimated_history_events=10 + 14 + 8 * 8,
    ),
    "fan-in": dict(
        task_count=17,
        counts=dict(compile=8, simulate=8, aggregate=1),
        chain_count=8,
        max_width=8,
        critical_path_timeout_bound_s=1200 + 100 + 600,
        peak_cpu_milli=8000,
        peak_memory_mib=8 * 1024,
        largest_cpu_milli=1000,
        largest_memory_mib=1024,
        checkpoint_tasks=0,
        estimated_history_events=10 + 8 * 14 + 8 * 8 + 8,
    ),
    "aggregate-only": dict(
        task_count=1,
        counts=dict(compile=0, simulate=0, aggregate=1),
        chain_count=0,
        max_width=1,
        critical_path_timeout_bound_s=600,
        peak_cpu_milli=1000,
        peak_memory_mib=512,
        largest_cpu_milli=1000,
        largest_memory_mib=512,
        checkpoint_tasks=0,
        estimated_history_events=18,
    ),
    "depends-only": dict(
        task_count=4,
        counts=dict(compile=2, simulate=2, aggregate=0),
        chain_count=2,
        max_width=2,
        critical_path_timeout_bound_s=1200 + 100,
        peak_cpu_milli=2000,
        peak_memory_mib=2048,
        largest_cpu_milli=1000,
        largest_memory_mib=1024,
        checkpoint_tasks=0,
        estimated_history_events=10 + 2 * 14 + 2 * 8,
    ),
}


@pytest.mark.parametrize("shape", sorted(GOLDEN))
def test_stats_golden(shape, shapes, plan_of, profile_of):
    assert stats(plan_of(shapes[shape]), profile_of("ci")).model_dump() == GOLDEN[shape]


def test_chains_follow_data_edges_only(shapes, plan_of):
    plan = plan_of(shapes["depends-only"])
    assert chains(plan) == {"b1": ["s1"], "b2": ["s2"]}
    assert next(t for t in plan.tasks if t.id == "s1").depends_on == ["b1", "b2"]


def test_a_simulate_with_no_data_edge_is_in_no_chain(shapes, plan_of):
    shapes = {**shapes["depends-only"]}
    tasks = {k: dict(v) for k, v in shapes["tasks"].items()}
    tasks["s1"]["inputs"] = [{"task": "b2", "output": "component"}]
    tasks["s1"]["depends_on"] = ["b1"]
    assert chains(plan_of({**shapes, "tasks": tasks})) == {"b1": [], "b2": ["s1", "s2"]}


def test_history_estimate(good, plan_of, profile_of):
    ci = profile_of("ci")
    plan = plan_of(good)
    expected = ci.history_events_per_job + sum(
        ci.history_events_per_task + ci.history_events_per_retry * t.retries for t in plan.tasks
    )
    assert stats(plan, ci).estimated_history_events == expected == 10 + (8 + 6) * 2 + 8


def test_checkpoint_tasks_are_counted(good, plan_of, profile_of):
    good["tasks"]["sim"]["checkpoint"] = True
    assert stats(plan_of(good), profile_of("ci")).checkpoint_tasks == 1


def test_the_peak_is_capped_by_max_parallel(shapes, plan_of, profile_of):
    tasks = {k: dict(v) for k, v in shapes["fan-out"]["tasks"].items()}
    for task in tasks.values():
        if task["type"] == "simulate":
            task["cpu"] = 0.5
    ci = profile_of("ci")
    wide = stats(plan_of({**shapes["fan-out"], "tasks": tasks, "max_parallel": 8}), ci)
    narrow = stats(plan_of({**shapes["fan-out"], "tasks": tasks, "max_parallel": 3}), ci)
    assert (wide.peak_cpu_milli, narrow.peak_cpu_milli) == (4000, 1500)
    assert narrow.max_width == 8
