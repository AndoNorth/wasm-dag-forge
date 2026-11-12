"""The job result: pause or fail of an exhausted task, and the status a job stops in."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from wdf_events.models import PartialData
from wdf_jobdoc.plan_models import Plan
from wdf_workflow.models import REASON_MAX
from wdf_workflow.ready import initial, resume, settle, to_place
from wdf_workflow.result import exhausted, outcome

HASH = "b" * 64
PARAMS = {
    "compile": {
        "source": {
            "uri": f"s3://wdf-sources/sha256/bb/{HASH}",
            "sha256": HASH,
            "size": 1,
            "name": "src.tar.gz",
            "kind": "source",
        },
        "entry": "sim",
    },
    "simulate": {"workload": "pi", "seed": 1},
    "aggregate": {"aggregation": "summary_stats"},
}
FLAG = {"compile": None, "simulate": "checkpoint", "aggregate": "allow_partial"}


def plan(tasks: dict[str, tuple]) -> Plan:
    """A bound plan from `{id: (type, depends_on, flag)}`, the flag being `checkpoint` or
    `allow_partial` by type."""
    rows = []
    for tid, (kind, deps, flag) in tasks.items():
        flags = dict.fromkeys(("checkpoint", "allow_partial"), False)
        if FLAG[kind]:
            flags[FLAG[kind]] = flag
        rows.append(
            {"id": tid, "type": kind, "pool": kind, "depends_on": deps, "inputs": [], **flags}
            | {"cpu_milli": 250, "memory_mib": 384, "run_timeout_s": 60, "retries": 0}
            | {"params": PARAMS[kind]}
        )
    return Plan.model_validate(
        {"plan_version": 1, "profile": "ci", "max_parallel": 4, "parameters": {}, "tasks": rows}
    )


# Two chains from one compile: sim-p checkpoints and sim-f does not, each with its aggregate.
CHAINS = {
    "build": ("compile", [], False),
    "sim-f": ("simulate", ["build"], False),
    "sim-p": ("simulate", ["build"], True),
    "agg-f": ("aggregate", ["sim-f"], False),
    "agg-p": ("aggregate", ["sim-p"], False),
}


def ended(p: Plan, **given) -> dict:
    """The states of a drained job: the tasks given end as given, and every other task that
    becomes ready succeeds."""
    states = settle(p, {t.id: given.get(t.id.replace("-", "_"), "pending") for t in p.tasks})
    while ready := [tid for tid, s in states.items() if s == "ready"]:
        states = settle(p, states | dict.fromkeys(ready, "succeeded"))
    return states


def test_exhausted_keys_on_checkpoint_flag():
    p = plan(CHAINS)
    assert exhausted(p, "sim-p", 0) == "paused"
    assert exhausted(p, "sim-f", 0) == "failed"
    assert exhausted(p, "build", 0) == "failed"
    assert exhausted(p, "agg-f", 3) == "failed"


def test_resume_compile_failure_pauses():
    p = plan(CHAINS)
    assert exhausted(p, "build", 1) == "paused"
    stopped = outcome(p, ended(p, build="paused"))
    assert (stopped.status, stopped.pause_reason) == ("paused", "resume-compile-failed")
    assert stopped.states["sim-p"] == "blocked"


def test_paused_and_failed_chain_pauses():
    p = plan(CHAINS)
    states = ended(p, sim_f="failed", sim_p="paused")
    assert states["agg-f"] == states["agg-p"] == "blocked"
    stopped = outcome(p, states)
    assert (stopped.status, stopped.pause_reason) == ("paused", "retries-exhausted")
    assert {tid: s for tid, s in stopped.states.items() if s != "succeeded"} == {
        "sim-f": "failed",
        "sim-p": "paused",
        "agg-f": "blocked",
        "agg-p": "blocked",
    }


def test_drain_before_pause():
    p = plan(CHAINS)
    states = settle(p, initial(p) | {"build": "succeeded", "sim-p": "paused"})
    assert to_place(p, states) == ["sim-f"]
    assert outcome(p, states) is None
    states = settle(p, states | {"sim-f": "running"})
    assert outcome(p, states) is None
    states = settle(p, states | {"sim-f": "succeeded"})
    assert outcome(p, states) is None
    states = settle(p, states | {"agg-f": "succeeded"})
    assert outcome(p, states).status == "paused"


def test_failed_chain_fails():
    p = plan(CHAINS)
    stopped = outcome(p, ended(p, sim_f="failed"))
    assert (stopped.status, stopped.reason) == ("failed", "retries-exhausted: sim-f")
    assert stopped.skipped_tasks == ()


def test_allow_partial_lists_skipped():
    p = plan(
        {
            "build": ("compile", [], False),
            "sim-1": ("simulate", ["build"], False),
            "sim-2": ("simulate", ["build"], False),
            "sim-3": ("simulate", ["build"], False),
            "report": ("aggregate", ["sim-1", "sim-2"], False),
            "agg": ("aggregate", ["sim-1", "sim-2", "sim-3"], True),
        }
    )
    states = settle(p, initial(p) | {"build": "succeeded", "sim-1": "succeeded"})
    states = settle(p, states | {"sim-2": "failed", "sim-3": "succeeded"})
    assert states["agg"] == "ready"
    assert states["report"] == "blocked"
    stopped = outcome(p, settle(p, states | {"agg": "succeeded"}))
    assert stopped.status == "partial"
    assert stopped.skipped_tasks == ("report", "sim-2")
    assert PartialData(skipped_tasks=list(stopped.skipped_tasks))


def test_allow_partial_over_every_input_succeeds():
    p = plan({"sim": ("simulate", [], False), "agg": ("aggregate", ["sim"], True)})
    assert outcome(p, ended(p)).status == "succeeded"


def test_skipped_compile_counts_as_succeeded():
    p = plan(CHAINS)
    assert outcome(p, ended(p, build="skipped")).status == "succeeded"


def test_resume_keeps_skipped_compile():
    p = plan(CHAINS)
    states = ended(p, build="skipped", sim_p="paused")
    resumed = resume(p, states)
    assert resumed["build"] == "skipped"
    assert to_place(p, resumed) == ["sim-p"]


def test_resume_reruns_unsucceeded():
    p = plan(CHAINS)
    states = ended(p, sim_f="failed", sim_p="paused")
    resumed = resume(p, states)
    assert {tid: s for tid, s in resumed.items() if s != "succeeded"} == {
        "sim-f": "ready",
        "sim-p": "ready",
        "agg-f": "pending",
        "agg-p": "pending",
    }
    assert outcome(p, resumed) is None


def test_manual_pause_waits_for_running_then_pauses():
    p = plan(CHAINS)
    states = settle(p, initial(p) | {"build": "succeeded", "sim-f": "running"})
    assert outcome(p, states, stop="pause") is None
    states = settle(p, states | {"sim-f": "succeeded"})
    stopped = outcome(p, states, stop="pause")
    assert (stopped.status, stopped.pause_reason) == ("paused", "operator")
    assert stopped.states["sim-p"] == "ready"


@pytest.mark.parametrize("stop", ["cancel", "terminate"])
def test_cancel_and_terminate_end_every_open_task(stop):
    p = plan(CHAINS)
    states = settle(p, initial(p) | {"build": "succeeded", "sim-f": "running"})
    stopped = outcome(p, states, stop=stop)
    if stop == "cancel":
        assert stopped is None
        stopped = outcome(p, states | {"sim-f": "cancelled"}, stop=stop)
    assert stopped.status == {"cancel": "cancelled", "terminate": "terminated"}[stop]
    assert stopped.states == {
        "build": "succeeded",
        "sim-f": "cancelled",
        "sim-p": "cancelled",
        "agg-f": "cancelled",
        "agg-p": "cancelled",
    }


@pytest.mark.parametrize("stop", ["cancel", "terminate"])
def test_cancel_and_terminate_of_a_paused_job_end_paused_tasks(stop):
    p = plan(CHAINS)
    stopped = outcome(p, ended(p, sim_p="paused"), stop=stop)
    assert stopped.states["sim-p"] == "cancelled"
    assert stopped.states["agg-p"] == "blocked"


@pytest.mark.parametrize("stop", ["pause", "cancel", "terminate"])
def test_finished_job_succeeds_whatever_the_request(stop):
    p = plan(CHAINS)
    assert outcome(p, ended(p), stop=stop).status == "succeeded"


def test_states_in_ascending_task_id():
    p = plan({"z": ("compile", [], False), "a": ("simulate", ["z"], False)})
    assert list(initial(p)) == ["a", "z"]
    assert list(outcome(p, ended(p, a="failed")).states) == ["a", "z"]


def test_fail_reason_ends_failed_after_running_tasks():
    p = plan(CHAINS)
    states = settle(p, initial(p) | {"build": "succeeded", "sim-f": "running"})
    assert outcome(p, states, fail_reason="history-budget: 10000") is None
    stopped = outcome(
        p, settle(p, states | {"sim-f": "succeeded"}), fail_reason="history-budget: 10000"
    )
    assert (stopped.status, stopped.reason) == ("failed", "history-budget: 10000")
    assert stopped.states["sim-p"] == "cancelled"


def test_failed_reason_is_clipped():
    ids = [f"sim-{n:03}-{'x' * 50}" for n in range(30)]
    p = plan({tid: ("simulate", [], False) for tid in ids})
    stopped = outcome(p, ended(p, **{tid.replace("-", "_"): "failed" for tid in ids}))
    assert len(stopped.reason) == REASON_MAX
    assert stopped.reason.startswith("retries-exhausted: sim-000")


TERMINAL = {"succeeded", "skipped", "failed", "blocked", "paused", "cancelled"}


class TestProperty:
    """The property tests, so that `pytest -k property` selects them."""

    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(st.sampled_from(["succeeded", "exhausted"]), min_size=5, max_size=5),
        st.booleans(),
        st.integers(0, 2),
    )
    def test_every_task_ends_with_a_state(self, ends, partial, resume_counter):
        p = plan(CHAINS | {"agg-f": ("aggregate", ["sim-f", "sim-p"], partial)})
        states = initial(p)
        draws = iter(ends)
        while ready := to_place(p, states):
            tid = ready[0]
            end = next(draws)
            if end == "exhausted":
                end = exhausted(p, tid, resume_counter)
            states = settle(p, states | {tid: end})
        stopped = outcome(p, states)
        assert set(stopped.states.values()) <= TERMINAL
        assert (stopped.status == "paused") == (stopped.pause_reason is not None)
        assert (stopped.status == "partial") == bool(stopped.skipped_tasks)
        assert (stopped.status == "failed") == (stopped.reason is not None)
