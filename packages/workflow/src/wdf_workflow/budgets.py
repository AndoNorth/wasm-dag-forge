"""The failure budgets of one task: what is left of each, the checkpoints kept and the backoffs.

Pure functions with no Temporal import, so the workflow and its tests share one rule.
A budget counts the retries it still allows, so a task with a budget of n runs n + 1 attempts
charged to it, and the status query shows what is left.
"""

from dataclasses import dataclass, replace

from wdf_artifactclient.checkpoint import MIN_KEEP
from wdf_events.models import CheckpointReference
from wdf_jobdoc.plan_models import PlanTask

# Estimates: a weekly rollout and a node loss cost one each, and a permanently broken pool stops
# within about an hour of backoffs.
INFRA_RETRIES = 10
INFRA_BACKOFF_FIRST_S = 10
INFRA_BACKOFF_MAX_S = 600


@dataclass(frozen=True)
class Budgets:
    """What a task may still spend in this run, between two resumes."""

    # The plan's `retries` of the task at the start of the run.
    guest_left: int
    infra_left: int
    # The schedule-to-start timeouts that expired, each followed by a new placement.
    reschedules: int = 0
    # The `oom_killed` failures in a row, which a failure of any other class ends, except a
    # schedule-to-start timeout, whose attempt never ran.
    oom_streak: int = 0
    # Whether the one run in a larger memory class was taken.
    escalated: bool = False
    # Whether a lost cached component was already rebuilt.
    rebuilt: bool = False
    # The class of the last failure, or None before any.
    last_class: str | None = None
    # The checkpoints of the task, newest first, as many as the checkpoint client keeps.
    checkpoints: tuple[CheckpointReference, ...] = ()

    @property
    def checkpoint(self) -> CheckpointReference | None:
        """The checkpoint the next attempt starts from, or None to start from the beginning."""
        return self.checkpoints[0] if self.checkpoints else None

    @property
    def infra_used(self) -> int:
        return INFRA_RETRIES - self.infra_left


def fresh(task: PlanTask) -> Budgets:
    """The budgets of a task at the start of a job."""
    return Budgets(guest_left=task.retries, infra_left=INFRA_RETRIES)


def resumed(task: PlanTask, budgets: Budgets) -> Budgets:
    """The budgets of a task after a resume: all fresh, and the checkpoints kept."""
    return replace(fresh(task), checkpoints=budgets.checkpoints)


def remember(budgets: Budgets, checkpoint: CheckpointReference | None) -> Budgets:
    """The budgets with a checkpoint the task reported at the head of the kept list.

    A reference already at the head is not kept twice, because heartbeats repeat the latest one.
    """
    if checkpoint is None or budgets.checkpoint == checkpoint:
        return budgets
    return replace(budgets, checkpoints=(checkpoint, *budgets.checkpoints)[:MIN_KEEP])


def infra_backoff_s(used: int) -> int:
    """The wait before the retry that the `used`-th infrastructure failure gives: 10 s doubling to
    10 min."""
    return min(INFRA_BACKOFF_FIRST_S * 2 ** (used - 1), INFRA_BACKOFF_MAX_S)


def next_schedule_to_start_s(expired_s: int, ceiling_s: int) -> int:
    """The schedule-to-start timeout after one expired: doubled, up to the ceiling."""
    return min(expired_s * 2, ceiling_s)
