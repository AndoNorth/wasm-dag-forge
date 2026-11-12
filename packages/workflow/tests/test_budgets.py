"""The budgets of one task: their start, a resume, the kept checkpoints and the backoffs."""

from dataclasses import replace

from wdf_events.models import CheckpointReference
from wdf_jobdoc.plan_models import Plan
from wdf_workflow.budgets import (
    INFRA_RETRIES,
    fresh,
    infra_backoff_s,
    next_schedule_to_start_s,
    remember,
    resumed,
)
from wdf_workflow.contract import pools

JOB = "0b7e3f4a-5c6d-4e8f-9a0b-1c2d3e4f5a6b"


def task(retries: int):
    return Plan.model_validate(
        {
            "plan_version": 1,
            "profile": "ci",
            "max_parallel": 1,
            "parameters": {},
            "tasks": [
                {
                    "id": "sim",
                    "type": "simulate",
                    "pool": "simulate",
                    "depends_on": [],
                    "inputs": [],
                    "cpu_milli": 250,
                    "memory_mib": 384,
                    "run_timeout_s": 60,
                    "retries": retries,
                    "checkpoint": True,
                    "allow_partial": False,
                    "params": {"workload": "pi", "seed": 1},
                }
            ],
        }
    ).tasks[0]


def checkpoint(attempt: int) -> CheckpointReference:
    return CheckpointReference(
        uri=f"s3://wdf-checkpoints/acme/b/{JOB}/7/sim/r0a{attempt}/0/checkpoint.json",
        sha256=f"{attempt:064x}",
        size=1,
        name="checkpoint.json",
        kind="checkpoint",
        producer={"job": JOB, "task": "sim", "attempt": f"r0a{attempt}"},
    )


def test_fresh_budgets_from_plan_retries():
    b = fresh(task(3))
    assert (b.guest_left, b.infra_left, b.reschedules, b.checkpoint) == (3, INFRA_RETRIES, 0, None)


def test_remember_keeps_two_newest_first():
    b = fresh(task(0))
    for attempt in (1, 2, 2, 3):
        b = remember(b, checkpoint(attempt))
    assert b.checkpoints == (checkpoint(3), checkpoint(2))
    assert remember(b, None) is b


def test_resume_refreshes_budgets_and_keeps_checkpoints():
    t = task(2)
    spent = replace(
        remember(fresh(t), checkpoint(1)),
        guest_left=0,
        infra_left=0,
        reschedules=4,
        oom_streak=1,
        escalated=True,
        rebuilt=True,
        last_class="timeout",
    )
    assert resumed(t, spent) == replace(fresh(t), checkpoints=(checkpoint(1),))


def test_infra_backoff_doubles_to_ten_minutes():
    waits = [infra_backoff_s(used) for used in range(1, INFRA_RETRIES + 1)]
    assert waits == [10, 20, 40, 80, 160, 320, 600, 600, 600, 600]


def test_schedule_to_start_doubles_to_ceiling():
    table = pools()
    timeout = table.pools["simulate"].schedule_to_start_s
    seen = []
    for _ in range(7):
        timeout = next_schedule_to_start_s(timeout, table.schedule_to_start_ceiling_s)
        seen.append(timeout)
    assert seen == [3600, 7200, 14400, 28800, 43200, 43200, 43200]
