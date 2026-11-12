"""The ready set of a job: which tasks may run, from the plan and the state of each task.

Pure functions with no Temporal import, so the workflow and its tests share one rule.
A state map holds the `TaskState` of every task of the plan, and each function returns a new map
in ascending task id, the order of the status query.
"""

from collections.abc import Iterable, Mapping

from wdf_jobdoc.plan_models import Plan

from .models import TaskState

States = Mapping[str, TaskState]

# A skipped compile was satisfied by a cache hit and counts as succeeded everywhere.
DONE = frozenset({"succeeded", "skipped"})
ACTIVE = frozenset({"pending", "ready", "running"})
# The states settle may change: a task that has not run, or that waits on its dependencies.
WAITING = frozenset({"pending", "ready", "blocked"})


def initial(plan: Plan) -> dict[str, TaskState]:
    """Every task pending, then settled, so the tasks without dependencies are ready."""
    return settle(plan, {t.id: "pending" for t in plan.tasks})


def settle(plan: Plan, states: States) -> dict[str, TaskState]:
    """Each waiting task moved to `ready`, `pending` or `blocked` by the states of its dependencies.

    A task is ready when every dependency is done, and blocked as soon as one failed, paused or
    is blocked.
    An aggregate with `allow_partial` instead waits until no dependency is active and then runs
    on the inputs that are done, but stays blocked behind a paused chain, which a resume runs
    again, and when none of its inputs is done.
    """
    # ponytail: one pass over the plan per call, O(tasks) per event; make it incremental if a
    # plan far above the ci max_tasks of 512 ever runs.
    out = dict(states)
    # A task that cannot finish in this run because a chain above it paused.
    held: set[str] = set()
    for task in plan.tasks:
        deps = [out[d] for d in task.depends_on]
        if out[task.id] == "paused" or any(d in held for d in task.depends_on):
            held.add(task.id)
        if out[task.id] not in WAITING:
            continue
        if all(d in DONE for d in deps):
            out[task.id] = "ready"
        elif task.allow_partial and task.id not in held:
            if any(d in ACTIVE for d in deps):
                out[task.id] = "pending"
            else:
                out[task.id] = "ready" if any(d in DONE for d in deps) else "blocked"
        elif any(d in ACTIVE for d in deps) and all(d in DONE | ACTIVE for d in deps):
            out[task.id] = "pending"
        else:
            out[task.id] = "blocked"
    return dict(sorted(out.items()))


def to_place(plan: Plan, states: States) -> list[str]:
    """The ready tasks to place now, in ascending task id, so that at most `max_parallel` run."""
    free = plan.max_parallel - sum(s == "running" for s in states.values())
    return sorted(tid for tid, s in states.items() if s == "ready")[: max(free, 0)]


def to_interrupt(plan: Plan, states: States) -> list[str]:
    """The running tasks a manual pause cancels: the checkpoint-capable ones, in ascending task id.

    Every other running task runs to completion, and only a simulate task can set `checkpoint`.
    """
    return sorted(t.id for t in plan.tasks if t.checkpoint and states[t.id] == "running")


def resume(plan: Plan, states: States, rerun: Iterable[str] = ()) -> dict[str, TaskState]:
    """The states a resume starts from: every task that is not done runs again.

    A done task keeps its state only while every dependency keeps its own, so an aggregate that
    ran on partial inputs runs again over the inputs that now run, and a compile in `rerun` (the
    recompile list of a patch) runs again with every task after it.
    """
    rerun = frozenset(rerun)
    out: dict[str, TaskState] = {}
    for task in plan.tasks:
        kept = (
            states[task.id] in DONE
            and task.id not in rerun
            and all(out[d] in DONE for d in task.depends_on)
        )
        out[task.id] = states[task.id] if kept else "pending"
    return settle(plan, out)
