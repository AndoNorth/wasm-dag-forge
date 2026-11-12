"""What a failed attempt leads to: the failure class table applied to one task and its budgets.

Pure functions with no Temporal import, so the workflow and its tests share one rule.
A task whose decision is `end` exhausted a budget, and `wdf_workflow.result.exhausted` says
whether it pauses or fails.
"""

from dataclasses import dataclass, replace
from typing import Literal

from wdf_events.models import CheckpointReference
from wdf_jobdoc.plan_models import PlanTask
from wdf_jobdoc.profile import Limits

from .budgets import Budgets, infra_backoff_s, next_schedule_to_start_s, remember
from .contract import failure_classes, pools
from .models import Budget

# retry: run again after the backoff. re-place: place again with a longer schedule-to-start
# timeout. rebuild: invalidate the cache entry, compile again, then run the task again. wait: run
# again after the resume. end: the task exhausted a budget.
Kind = Literal["retry", "re-place", "rebuild", "wait", "end"]

LOST = "ARTIFACT_LOST"


@dataclass(frozen=True)
class Failure:
    """One failed attempt, as the workflow reads it from the error and its details."""

    # A row of the failure class table: the ApplicationError type, or the class the workflow
    # gives a timeout or an Unschedulable verdict.
    class_: str
    # The deterministic flag of the failure details, read only on a `from-error-file` row.
    deterministic: bool | None = None
    # The latest checkpoint of the failure details or of the last heartbeat.
    checkpoint: CheckpointReference | None = None
    # Whether the lost object is the component of a compile cache hit of this job.
    cached_component_lost: bool = False
    # Whether the placer offers the task a larger memory class.
    larger_class: bool = False
    # The schedule-to-start timeout that expired, on a `schedule_to_start_timeout`.
    schedule_to_start_s: int | None = None


@dataclass(frozen=True)
class Decision:
    kind: Kind
    # The class decided on, which is ARTIFACT_LOST when no checkpoint is left to fall back to.
    class_: str
    # The budgets of the task after this failure.
    budgets: Budgets
    # The budget this failure consumed, or None when it consumed nothing.
    charged: Budget | None = None
    backoff_s: int = 0
    # Whether the next attempt runs in a larger memory class.
    escalate: bool = False
    # The schedule-to-start timeout of the next placement, on `re-place`.
    schedule_to_start_s: int | None = None
    # The history events the decision is estimated to add, from the plan's limits profile.
    history_events: int = 0

    @property
    def checkpoint(self) -> CheckpointReference | None:
        """The checkpoint the next attempt starts from."""
        return self.budgets.checkpoint


def decide(failure: Failure, task: PlanTask, budgets: Budgets, limits: Limits) -> Decision:
    """The decision for one failed attempt of a task, and the budgets it leaves.

    An action replaces the budget rule for the occurrence it applies to.
    During a manual pause a schedule-to-start timeout is not a failure and never reaches here:
    the task returns to `ready` to be placed after the resume.
    Raises ValueError for a class the table lacks, a workload class of another pool than the
    task's, and a `from-error-file` failure without its deterministic flag, all of them defects
    of the caller or the worker.
    """
    row = failure_classes().get(failure.class_)
    if row is None:
        raise ValueError(f"{failure.class_} is not a row of the failure class table")
    if row.pool is not None and row.pool != task.pool:
        raise ValueError(f"{failure.class_} is a {row.pool} class, not one of {task.pool}")
    if row.deterministic == "from-error-file" and failure.deterministic is None:
        raise ValueError(f"{failure.class_} needs the deterministic flag of its details")

    oom = failure.class_ == "oom_killed"
    streak = budgets.oom_streak + 1 if oom else budgets.oom_streak
    if not oom and failure.class_ != "schedule_to_start_timeout":
        streak = 0
    b = replace(remember(budgets, failure.checkpoint), last_class=failure.class_, oom_streak=streak)
    retry = limits.history_events_per_retry

    def made(kind: Kind, budgets: Budgets, class_: str = failure.class_, **fields) -> Decision:
        history = {"end": 0, "rebuild": limits.history_events_per_task + retry}.get(kind, retry)
        return Decision(kind, class_, budgets, history_events=history, **fields)

    def escalation() -> Decision | None:
        if failure.larger_class and not b.escalated:
            return made("retry", replace(b, escalated=True), escalate=True)
        return None

    if row.action == "previous-checkpoint":
        if len(b.checkpoints) > 1:
            return made("retry", replace(b, checkpoints=b.checkpoints[1:]))
        return made("end", replace(b, checkpoints=(), last_class=LOST), LOST)
    if row.action == "invalidate-cache-and-rebuild":
        if failure.cached_component_lost and not b.rebuilt:
            return made("rebuild", replace(b, rebuilt=True))
        return made("end", b)
    if row.action == "re-place":
        ceiling = pools().schedule_to_start_ceiling_s
        if failure.schedule_to_start_s is None:
            raise ValueError("a schedule-to-start timeout needs the timeout that expired")
        timeout = next_schedule_to_start_s(failure.schedule_to_start_s, ceiling)
        return made(
            "re-place", replace(b, reschedules=b.reschedules + 1), schedule_to_start_s=timeout
        )
    if row.action == "escalate-once" and (escalated := escalation()):
        return escalated
    if oom and streak >= 2:
        # A pod too small for its task is killed at the same point on every attempt.
        return escalation() or made("end", replace(b, infra_left=0))

    budget = row.budget
    if row.deterministic == "from-error-file" and failure.deterministic:
        budget = "none"
    if budget == "free":
        # The one free row without an action is operator_interrupt, which runs after the resume.
        return made("wait", b)
    if budget == "guest":
        if b.guest_left == 0:
            return made("end", b)
        return made("retry", replace(b, guest_left=b.guest_left - 1), charged="guest")
    if budget == "infra":
        if b.infra_left == 0:
            return made("end", b)
        b = replace(b, infra_left=b.infra_left - 1)
        return made("retry", b, charged="infra", backoff_s=infra_backoff_s(b.infra_used))
    return made("end", b)
