"""The ready set: dependencies, max_parallel, fan-out and fan-in, blocked dependents, aggregate
readiness, the manual pause and the resume."""

from hypothesis import given, settings
from hypothesis import strategies as st
from wdf_jobdoc.plan_models import Plan
from wdf_workflow.ready import DONE, initial, resume, settle, to_interrupt, to_place
from wdf_workflow.result import exhausted, outcome

HASH = "a" * 64
SOURCE = {
    "uri": f"s3://wdf-sources/sha256/aa/{HASH}",
    "sha256": HASH,
    "size": 2048,
    "name": "bundle.tar.gz",
    "kind": "source",
}
PARAMS = {
    "compile": {"source": SOURCE, "entry": "sim"},
    "simulate": {"workload": "pi", "args": {}, "seed": 7},
    "aggregate": {"aggregation": "digest_list"},
}


def plan(tasks: dict[str, tuple], max_parallel: int = 8) -> Plan:
    """A bound plan from `{id: (type, depends_on, flag)}`, where the flag is the `checkpoint` of a
    simulate task and the `allow_partial` of an aggregate, in the order given."""
    return Plan.model_validate(
        {
            "plan_version": 1,
            "profile": "ci",
            "max_parallel": max_parallel,
            "parameters": {},
            "tasks": [
                {
                    "id": tid,
                    "type": kind,
                    "pool": kind,
                    "depends_on": sorted(deps),
                    "inputs": [],
                    "cpu_milli": 250,
                    "memory_mib": 384,
                    "run_timeout_s": 600,
                    "retries": 1,
                    "checkpoint": kind == "simulate" and flag,
                    "allow_partial": kind == "aggregate" and flag,
                    "params": PARAMS[kind],
                }
                for tid, (kind, deps, flag) in tasks.items()
            ],
        }
    )


# A compile, four simulate tasks over it and an aggregate over them.
FAN = {
    "build": ("compile", [], False),
    "sim-a": ("simulate", ["build"], False),
    "sim-b": ("simulate", ["build"], True),
    "sim-c": ("simulate", ["build"], False),
    "sim-d": ("simulate", ["build"], True),
    "agg": ("aggregate", ["sim-a", "sim-b", "sim-c", "sim-d"], False),
}


def run(p: Plan, states, *ids: str, to: str = "running"):
    return settle(p, {**states, **dict.fromkeys(ids, to)})


def test_initial_readies_roots_only():
    p = plan(FAN)
    assert initial(p) == {"build": "ready"} | dict.fromkeys(list(FAN)[1:], "pending")


def test_fan_out_under_max_parallel():
    p = plan(FAN, max_parallel=2)
    states = run(p, initial(p), "build", to="succeeded")
    assert to_place(p, states) == ["sim-a", "sim-b"]
    states = run(p, states, "sim-a", "sim-b")
    assert to_place(p, states) == []
    states = run(p, states, "sim-b", to="succeeded")
    assert to_place(p, states) == ["sim-c"]


def test_ready_set_in_ascending_task_id():
    p = plan(
        {"z": ("compile", [], False), "b": ("compile", [], False), "m": ("compile", [], False)}
    )
    assert [t.id for t in p.tasks] == ["z", "b", "m"]
    assert to_place(p, initial(p)) == ["b", "m", "z"]


def test_aggregate_waits_for_all():
    p = plan(FAN)
    states = run(p, initial(p), "build", "sim-a", "sim-b", "sim-c", to="succeeded")
    assert states["agg"] == "pending"
    states = run(p, states, "sim-d")
    assert states["agg"] == "pending"
    states = run(p, states, "sim-d", to="failed")
    assert states["agg"] == "blocked"
    states = run(p, states, "sim-d", to="succeeded")
    assert states["agg"] == "ready"


def test_blocked_dependents_while_branches_drain():
    p = plan(FAN | {"report": ("aggregate", ["agg"], False)})
    states = run(p, initial(p), "build", to="succeeded")
    states = run(p, states, "sim-a", to="failed")
    assert states["agg"] == states["report"] == "blocked"
    assert to_place(p, states) == ["sim-b", "sim-c", "sim-d"]


def test_allow_partial_runs_once_inputs_settle():
    p = plan(FAN | {"agg": ("aggregate", FAN["agg"][1], True)})
    states = run(p, initial(p), "build", "sim-b", "sim-c", to="succeeded")
    states = run(p, states, "sim-a", to="failed")
    states = run(p, states, "sim-d")
    assert states["agg"] == "pending"
    states = run(p, states, "sim-d", to="failed")
    assert states["agg"] == "ready"


def test_allow_partial_blocked_without_a_done_input():
    p = plan({"sim": ("simulate", [], False), "agg": ("aggregate", ["sim"], True)})
    assert run(p, initial(p), "sim", to="failed")["agg"] == "blocked"


def test_allow_partial_blocked_behind_a_paused_chain():
    p = plan(FAN | {"agg": ("aggregate", FAN["agg"][1], True)})
    states = run(p, initial(p), "build", "sim-a", "sim-c", to="succeeded")
    states = run(p, states, "sim-d", to="failed")
    states = run(p, states, "sim-b", to="paused")
    assert states["agg"] == "blocked"


def test_manual_pause_cancels_only_checkpoint_tasks():
    p = plan(FAN)
    states = run(p, initial(p), "build", to="succeeded")
    states = run(p, states, "sim-a", "sim-b", "sim-d")
    assert to_interrupt(p, states) == ["sim-b", "sim-d"]
    assert to_interrupt(p, run(p, initial(p), "build")) == []


def test_resume_reruns_unsucceeded():
    p = plan(FAN | {"report": ("aggregate", ["agg"], False)})
    states = run(p, initial(p), "build", "sim-a", to="succeeded")
    states = run(p, states, "sim-b", to="paused")
    states = run(p, states, "sim-c", to="failed")
    states = run(p, states, "sim-d", to="succeeded")
    assert resume(p, states) == {
        "build": "succeeded",
        "sim-a": "succeeded",
        "sim-b": "ready",
        "sim-c": "ready",
        "sim-d": "succeeded",
        "agg": "pending",
        "report": "pending",
    }


def test_resume_keeps_skipped_compile():
    p = plan(FAN)
    states = run(p, initial(p), "build", to="skipped")
    states = run(p, states, "sim-a", "sim-c", "sim-d", to="succeeded")
    states = run(p, states, "sim-b", to="paused")
    resumed = resume(p, states)
    assert resumed["build"] == "skipped"
    assert resumed["sim-b"] == "ready"
    assert [tid for tid, s in resumed.items() if s not in DONE] == ["agg", "sim-b"]


def test_resume_reruns_partial_aggregate_and_patched_compile():
    p = plan(FAN | {"agg": ("aggregate", FAN["agg"][1], True)})
    states = run(p, initial(p), "build", "sim-a", "sim-b", "sim-c", "agg", to="succeeded")
    states = run(p, states, "sim-d", to="failed")
    assert resume(p, states)["agg"] == "pending"
    resumed = resume(p, states, rerun=["build"])
    assert resumed["build"] == "ready"
    assert all(s == "pending" for tid, s in resumed.items() if tid != "build")


# Ids drawn at random, so the plan order and the ascending id order differ.
IDS = st.lists(st.from_regex(r"[a-z][a-z0-9]{0,5}", fullmatch=True), min_size=1, unique=True)


@st.composite
def plans(draw):
    """A plan of up to 12 tasks whose dependencies point to earlier tasks."""
    ids = draw(IDS.filter(lambda ids: len(ids) <= 12))
    tasks = {}
    for n, tid in enumerate(ids):
        deps = draw(st.lists(st.sampled_from(ids[:n]), unique=True)) if n else []
        kind = draw(st.sampled_from(["simulate", "aggregate"] if deps else ["compile", "simulate"]))
        tasks[tid] = (kind, deps, draw(st.booleans()))
    return plan(tasks, max_parallel=draw(st.integers(1, 4)))


def drive(p: Plan, data, resume_counter: int = 0):
    """Run `p` to its outcome, ending a running task chosen by `data` with a drawn result at each
    step, and check the ready set and max_parallel at every step."""
    states = initial(p)
    tasks = {t.id: t for t in p.tasks}
    placed = []
    while True:
        for tid in to_place(p, states):
            placed.append(tid)
            states = run(p, states, tid)
        running = sorted(tid for tid, s in states.items() if s == "running")
        assert len(running) <= p.max_parallel
        for tid, s in states.items():
            if s in ("ready", "running"):
                deps = [states[d] for d in tasks[tid].depends_on]
                if tasks[tid].allow_partial:
                    assert all(d in DONE | {"failed", "blocked"} for d in deps)
                else:
                    assert all(d in DONE for d in deps), (tid, states)
        if not running:
            return states, placed
        tid = data.draw(st.sampled_from(running))
        end = data.draw(st.sampled_from(["succeeded", "exhausted"]))
        if end == "exhausted":
            end = exhausted(p, tid, resume_counter)
        states = run(p, states, tid, to=end)


class TestProperty:
    """The property tests, so that `pytest -k property` selects them."""

    @settings(max_examples=200, deadline=None)
    @given(plans(), st.data())
    def test_dependencies_and_max_parallel_hold(self, p, data):
        states, _ = drive(p, data)
        stopped = outcome(p, states)
        assert stopped is not None
        assert set(stopped.states) == {t.id for t in p.tasks}
        assert not {"pending", "ready", "running"} & set(stopped.states.values())

    @settings(max_examples=100, deadline=None)
    @given(plans(), st.randoms(use_true_random=False))
    def test_order_is_a_function_of_the_plan(self, p, rnd):
        states = initial(p)
        while placed := to_place(p, states):
            ids = list(states)
            rnd.shuffle(ids)
            assert to_place(p, {tid: states[tid] for tid in ids}) == placed == sorted(placed)
            states = run(p, states, *placed, to="succeeded")
        assert set(states.values()) == {"succeeded"}

    @settings(max_examples=100, deadline=None)
    @given(plans(), st.data())
    def test_resume_runs_every_unsucceeded_task_again(self, p, data):
        states, _ = drive(p, data)
        resumed = resume(p, states)
        for task in p.tasks:
            if states[task.id] not in DONE:
                assert resumed[task.id] in ("pending", "ready")
            elif resumed[task.id] not in DONE:
                assert any(resumed[d] not in DONE for d in task.depends_on)
