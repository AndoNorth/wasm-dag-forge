"""The decision of each failure class: the budget it consumes, its action and its backoff."""

from dataclasses import replace

import pytest
from wdf_events.models import CheckpointReference
from wdf_jobdoc.plan_models import Plan
from wdf_workflow.activities.plan import limits
from wdf_workflow.budgets import INFRA_RETRIES, Budgets, fresh, remember, resumed
from wdf_workflow.contract import failure_classes, pools
from wdf_workflow.failures import Failure, decide

JOB = "0b7e3f4a-5c6d-4e8f-9a0b-1c2d3e4f5a6b"
HASH = "b" * 64
LIMITS = limits().profiles["ci"]
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


def task(kind: str, retries: int = 2):
    row = {
        "id": kind,
        "type": kind,
        "pool": kind,
        "depends_on": [],
        "inputs": [],
        "cpu_milli": 250,
        "memory_mib": 384,
        "run_timeout_s": 60,
        "retries": retries,
        "checkpoint": kind == "simulate",
        "allow_partial": False,
        "params": PARAMS[kind],
    }
    plan = {"plan_version": 1, "profile": "ci", "max_parallel": 1, "parameters": {}}
    return Plan.model_validate(plan | {"tasks": [row]}).tasks[0]


def checkpoint(attempt: int) -> CheckpointReference:
    return CheckpointReference(
        uri=f"s3://wdf-checkpoints/acme/b/{JOB}/7/sim/r0a{attempt}/0/checkpoint.json",
        sha256=f"{attempt:064x}",
        size=1,
        name="checkpoint.json",
        kind="checkpoint",
        producer={"job": JOB, "task": "sim", "attempt": f"r0a{attempt}"},
    )


def started(kind: str = "simulate", retries: int = 2) -> Budgets:
    """Fresh budgets of a task that has written two checkpoints."""
    return remember(remember(fresh(task(kind, retries)), checkpoint(1)), checkpoint(2))


def failure(class_: str, **fields) -> Failure:
    """A failure as a worker of the right pool reports it, with the expired timeout on a
    schedule-to-start timeout and the deterministic flag on a from-error-file row."""
    if class_ == "schedule_to_start_timeout":
        fields.setdefault("schedule_to_start_s", 1800)
    if failure_classes()[class_].deterministic == "from-error-file":
        fields.setdefault("deterministic", False)
    return Failure(class_, **fields)


def run(class_: str, budgets: Budgets | None = None, kind: str | None = None, **fields):
    kind = kind or failure_classes()[class_].pool or "simulate"
    budgets = budgets or started(kind)
    return decide(failure(class_, **fields), task(kind), budgets, LIMITS)


# The first failure of each class on a task with fresh budgets and two checkpoints: the decision,
# the budget consumed and the backoff in seconds.
CASES = {
    "timeout": ("retry", "guest", 0),
    "guest_memory_cap": ("end", None, 0),
    "guest_failed": ("retry", "guest", 0),
    "host_error": ("retry", "infra", 10),
    "oom_killed": ("retry", "infra", 10),
    "compile_failed": ("end", None, 0),
    "compile_dependency_unavailable": ("retry", "infra", 10),
    "compile_timeout": ("retry", "guest", 0),
    "compile_oom": ("retry", "guest", 0),
    "aggregate_failed": ("retry", "guest", 0),
    "aggregate_input_unavailable": ("retry", "infra", 10),
    "aggregate_input_corrupt": ("end", None, 0),
    "aggregate_input_refused": ("end", None, 0),
    "aggregate_parse_failed": ("end", None, 0),
    "aggregate_oom": ("retry", "guest", 0),
    "aggregate_timeout": ("retry", "guest", 0),
    "STORE_UNAVAILABLE": ("retry", "infra", 10),
    "STORE_FORBIDDEN": ("end", None, 0),
    "ARTIFACT_NOT_FOUND": ("end", None, 0),
    "ARTIFACT_CORRUPT": ("end", None, 0),
    "ARTIFACT_LOST": ("end", None, 0),
    "OUTPUT_UPLOAD_FAILED": ("retry", "infra", 10),
    "OUTPUT_MISSING": ("end", None, 0),
    "CHECKPOINT_CORRUPT": ("retry", None, 0),
    "operator_interrupt": ("wait", None, 0),
    "worker_shutdown": ("retry", "infra", 10),
    "heartbeat_timeout": ("retry", "infra", 10),
    "start_to_close_timeout": ("retry", "infra", 10),
    "schedule_to_start_timeout": ("re-place", None, 0),
    "insufficient_memory": ("end", None, 0),
    "insufficient_cpu": ("end", None, 0),
    "no_matching_pool": ("end", None, 0),
}


def test_cases_are_the_table():
    assert set(CASES) == set(failure_classes())


@pytest.mark.parametrize("class_", sorted(failure_classes()))
def test_every_class_row_has_a_case(class_):
    before = started(failure_classes()[class_].pool or "simulate")
    decision = run(class_, before)
    assert (decision.kind, decision.charged, decision.backoff_s) == CASES[class_]
    assert decision.budgets.last_class == class_
    left = {"guest": before.guest_left, "infra": before.infra_left}
    if decision.charged:
        left[decision.charged] -= 1
    assert (decision.budgets.guest_left, decision.budgets.infra_left) == (
        left["guest"],
        left["infra"],
    )
    expected = (checkpoint(1),) if class_ == "CHECKPOINT_CORRUPT" else before.checkpoints
    assert decision.budgets.checkpoints == expected


def test_deterministic_class_not_retried():
    deterministic = [c for c, row in failure_classes().items() if row.deterministic is True]
    assert len(deterministic) == 13
    for class_ in deterministic:
        before = started(failure_classes()[class_].pool or "simulate")
        decision = run(class_, before)
        assert (decision.kind, decision.charged, decision.history_events) == ("end", None, 0)
        assert (decision.budgets.guest_left, decision.budgets.infra_left) == (2, INFRA_RETRIES)
    decision = run("guest_failed", deterministic=True)
    assert (decision.kind, decision.charged, decision.budgets.guest_left) == ("end", None, 2)


def test_guest_budget_exhausted():
    b = started(retries=2)
    for left in (1, 0):
        decision = run("guest_failed", b)
        assert (decision.kind, decision.budgets.guest_left) == ("retry", left)
        b = decision.budgets
    decision = run("timeout", b)
    assert (decision.kind, decision.charged) == ("end", None)
    assert decision.budgets.infra_left == INFRA_RETRIES


def test_infra_cap_exhausted():
    # Every infrastructure class draws on one budget, and the guest budget stays whole.
    classes = ["host_error", "heartbeat_timeout", "STORE_UNAVAILABLE", "worker_shutdown"]
    b = started()
    waits = []
    for n in range(INFRA_RETRIES):
        decision = run(classes[n % len(classes)], b, kind="simulate")
        assert (decision.kind, decision.charged) == ("retry", "infra")
        waits.append(decision.backoff_s)
        b = decision.budgets
    assert waits == [10, 20, 40, 80, 160, 320, 600, 600, 600, 600]
    assert (b.infra_left, b.guest_left) == (0, 2)
    decision = run("start_to_close_timeout", b)
    assert (decision.kind, decision.charged, decision.history_events) == ("end", None, 0)
    assert run("timeout", b).kind == "retry"


def test_operator_interrupt_free():
    b = replace(started(), guest_left=0, infra_left=0)
    for attempt in range(3, 8):
        decision = run("operator_interrupt", b, checkpoint=checkpoint(attempt))
        assert (decision.kind, decision.charged) == ("wait", None)
        assert decision.checkpoint == checkpoint(attempt)
        b = decision.budgets
    assert (b.guest_left, b.infra_left, b.checkpoints) == (0, 0, (checkpoint(7), checkpoint(6)))


def test_repeated_oom_killed_stops():
    first = run("oom_killed")
    assert (first.kind, first.charged, first.budgets.infra_left) == ("retry", "infra", 9)
    second = run("oom_killed", first.budgets)
    assert (second.kind, second.charged, second.budgets.infra_left) == ("end", None, 0)


def test_repeated_oom_killed_escalates_once():
    first = run("oom_killed", larger_class=True)
    assert not first.escalate
    second = run("oom_killed", first.budgets, larger_class=True)
    assert (second.kind, second.escalate, second.charged) == ("retry", True, None)
    third = run("oom_killed", second.budgets, larger_class=True)
    assert (third.kind, third.budgets.infra_left) == ("end", 0)


def test_oom_streak_ends_on_another_attempt_but_not_on_a_reschedule():
    b = run("oom_killed").budgets
    b = run("host_error", b).budgets
    assert run("oom_killed", b).kind == "retry"
    b = run("oom_killed", started()).budgets
    b = run("schedule_to_start_timeout", b).budgets
    assert run("oom_killed", b).kind == "end"


@pytest.mark.parametrize("class_", ["compile_oom", "aggregate_oom"])
def test_guest_oom_escalates_once_when_a_larger_class_exists(class_):
    first = run(class_, larger_class=True)
    assert (first.kind, first.escalate, first.charged) == ("retry", True, None)
    second = run(class_, first.budgets, larger_class=True)
    assert (second.kind, second.escalate, second.charged) == ("retry", False, "guest")


def test_schedule_to_start_backoff_ceiling():
    b = replace(started(), guest_left=0, infra_left=0)
    timeout = pools().pools["simulate"].schedule_to_start_s
    timeouts = []
    for _ in range(25):
        decision = run("schedule_to_start_timeout", b, schedule_to_start_s=timeout)
        assert (decision.kind, decision.charged) == ("re-place", None)
        timeout = decision.schedule_to_start_s
        timeouts.append(timeout)
        b = decision.budgets
    assert timeouts[:6] == [3600, 7200, 14400, 28800, 43200, 43200]
    assert set(timeouts[4:]) == {pools().schedule_to_start_ceiling_s}
    assert (b.reschedules, b.guest_left, b.infra_left) == (25, 0, 0)


def test_lost_cached_component_rebuilds_once_per_run():
    t = task("simulate")
    first = run("ARTIFACT_LOST", cached_component_lost=True)
    assert (first.kind, first.charged) == ("rebuild", None)
    second = run("ARTIFACT_LOST", first.budgets, cached_component_lost=True)
    assert second.kind == "end"
    again = run("ARTIFACT_LOST", resumed(t, second.budgets), cached_component_lost=True)
    assert again.kind == "rebuild"


def test_corrupt_checkpoint_falls_back_then_is_lost():
    first = run("CHECKPOINT_CORRUPT", checkpoint=checkpoint(2))
    assert (first.kind, first.checkpoint) == ("retry", checkpoint(1))
    second = run("CHECKPOINT_CORRUPT", first.budgets, checkpoint=checkpoint(1))
    assert (second.kind, second.class_, second.checkpoint) == ("end", "ARTIFACT_LOST", None)
    assert second.budgets.last_class == "ARTIFACT_LOST"


def test_failure_checkpoint_is_the_next_start():
    decision = run("host_error", checkpoint=checkpoint(3))
    assert decision.checkpoint == checkpoint(3)
    assert decision.budgets.checkpoints == (checkpoint(3), checkpoint(2))


def test_history_estimate_per_decision():
    retry, task_events = LIMITS.history_events_per_retry, LIMITS.history_events_per_task
    assert run("timeout").history_events == retry
    assert run("schedule_to_start_timeout").history_events == retry
    assert run("operator_interrupt").history_events == retry
    assert run("ARTIFACT_LOST", cached_component_lost=True).history_events == task_events + retry
    assert run("compile_failed").history_events == 0


@pytest.mark.parametrize(
    ("class_", "kind", "fields", "message"),
    [
        ("no_such_class", "simulate", {}, "not a row"),
        ("compile_oom", "simulate", {}, "compile class"),
        ("guest_failed", "simulate", {"deterministic": None}, "deterministic flag"),
        ("schedule_to_start_timeout", "simulate", {"schedule_to_start_s": None}, "expired"),
    ],
)
def test_defects_raise(class_, kind, fields, message):
    with pytest.raises(ValueError, match=message):
        decide(Failure(class_, **fields), task(kind), started(kind), LIMITS)
