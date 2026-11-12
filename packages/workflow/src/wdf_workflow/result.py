"""The job result: whether a task that exhausted a budget pauses or fails, and how a job ends.

Pure functions with no Temporal import, over the state map of `wdf_workflow.ready`.
"""

from dataclasses import dataclass
from typing import Literal

from wdf_jobdoc.plan_models import Plan

from .models import REASON_MAX, TaskState
from .ready import ACTIVE, DONE, States

JobStatus = Literal["succeeded", "partial", "failed", "cancelled", "terminated", "paused"]
PauseReason = Literal["operator", "retries-exhausted", "resume-compile-failed"]
Stop = Literal["pause", "cancel", "terminate"]


@dataclass(frozen=True)
class Outcome:
    """Where a job stands once nothing runs: a terminal status, or `paused` until a resume."""

    status: JobStatus
    # Set exactly when the status is paused.
    pause_reason: PauseReason | None
    # The failed reason, set exactly when the status is failed.
    reason: str | None
    # Every task that is neither succeeded nor skipped, set exactly when the status is partial.
    skipped_tasks: tuple[str, ...]
    # The state of every task when the job stops, in ascending task id, none of them pending,
    # ready or running unless the job paused, when a task keeps its state for the resume.
    states: dict[str, TaskState]


def exhausted(plan: Plan, task_id: str, resume_counter: int) -> TaskState:
    """The state of a task that exhausted a budget: `paused` or `failed`.

    The plan's `checkpoint` flag decides, not whether a checkpoint was written.
    A compile that runs after a resume pauses whatever its flag, so a job an operator just tried
    to fix keeps its checkpoints.
    """
    task = next(t for t in plan.tasks if t.id == task_id)
    if task.checkpoint or (task.type == "compile" and resume_counter > 0):
        return "paused"
    return "failed"


def outcome(
    plan: Plan, states: States, stop: Stop | None = None, fail_reason: str | None = None
) -> Outcome | None:
    """How the job stops, from settled states, or None while it must wait.

    A job whose every task is done succeeds, whatever the request in force.
    `stop` is the control request in force, and `fail_reason` ends the job failed once nothing
    runs, as the history budget does.
    Terminate stops at once and cancel once nothing runs, and both, like a failure, cancel every
    task that would still run, paused tasks included.
    A manual pause waits for its running tasks and then pauses with reason `operator`, even when a
    task also exhausted a budget, because a task that the pause interrupted is paused as well.
    Otherwise the job stops when nothing runs or is ready: it pauses when a task paused, with
    `resume-compile-failed` when one of them is a compile, succeeds when every task is done, ends
    `partial` when an aggregate with `allow_partial` ran over an input that is not done, and fails
    otherwise.
    The states of the outcome are in ascending task id, the order of the status query.
    """
    states = dict(sorted(states.items()))
    undone = [tid for tid, s in states.items() if s not in DONE]
    if not undone:
        return Outcome("succeeded", None, None, (), states)
    running = "running" in states.values()
    if stop == "terminate" or (stop == "cancel" and not running):
        status = "terminated" if stop == "terminate" else "cancelled"
        return Outcome(status, None, None, (), _ended(states))
    if running or (stop is None and fail_reason is None and "ready" in states.values()):
        return None
    if fail_reason is not None:
        return Outcome("failed", None, _clip(fail_reason), (), _ended(states))
    if stop == "pause":
        return Outcome("paused", "operator", None, (), states)
    paused = [t for t in plan.tasks if states[t.id] == "paused"]
    if paused:
        compile_failed = any(t.type == "compile" for t in paused)
        reason = "resume-compile-failed" if compile_failed else "retries-exhausted"
        return Outcome("paused", reason, None, (), states)
    if any(
        t.allow_partial
        and states[t.id] in DONE
        and any(states[d] not in DONE for d in t.depends_on)
        for t in plan.tasks
    ):
        return Outcome("partial", None, None, tuple(undone), states)
    failed = ", ".join(tid for tid in undone if states[tid] == "failed")
    return Outcome("failed", None, _clip(f"retries-exhausted: {failed}"), (), states)


def _ended(states: States) -> dict[str, TaskState]:
    """The states of a job the workflow ended: every task that would still run is cancelled."""
    return {tid: "cancelled" if s in ACTIVE | {"paused"} else s for tid, s in states.items()}


def _clip(reason: str) -> str:
    return reason if len(reason) <= REASON_MAX else reason[: REASON_MAX - 3] + "..."
