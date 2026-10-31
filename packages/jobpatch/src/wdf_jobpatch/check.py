"""Whether a changed plan may replace the plan of a paused job.

Both plans are bound, so every task holds the resolved values of one job.
Anything the patch rule does not name as changeable is frozen, which keeps the rule closed: a field
added to the plan later is frozen until the rule says otherwise.
"""

from dataclasses import dataclass

from wdf_jobdoc.issues import Issue, ordered, pointer
from wdf_jobdoc.plan_models import Plan
from wdf_plan import chains

# The parameters of unfinished work that a patch may change, by task type.
CHANGEABLE = {
    "compile": {"source", "entry"},
    "simulate": {"workload", "args", "seed"},
    "aggregate": {"aggregation", "args"},
}


@dataclass(frozen=True)
class PatchReport:
    compatible: bool
    issues: tuple[Issue, ...]
    # Tasks whose changed parameters are accepted and listed for the checkpoint and dispatch logic.
    changed: tuple[str, ...]
    # Compile tasks whose bundle changed and that run again.
    recompile: tuple[str, ...]


def _dump(model) -> dict:
    return model.model_dump(mode="json", exclude_none=True)


def _differing(old: dict, new: dict, skip: str) -> list[str]:
    """The sorted keys whose values differ between two mappings, leaving out `skip`."""
    keys = (old.keys() | new.keys()) - {skip}
    return sorted(k for k in keys if old.get(k) != new.get(k))


def _frozen(path: tuple, message: str) -> Issue:
    return Issue("frozen-field-changed", pointer(path), message)


def _task_issues(tid, old, new, completed, in_result_chain, found):
    """Classify one task present in both plans, adding to `found`: issues, changed, recompile."""
    old_task, new_task = _dump(old), _dump(new)
    if old_task == new_task:
        return
    path = ("tasks", tid)
    if old.type != "compile" and tid in completed:
        found["issues"].append(
            Issue("completed-task-changed", pointer(path), f"the {old.type} task already completed")
        )
        return
    for field in _differing(old_task, new_task, "params"):
        found["issues"].append(_frozen((*path, field), "a patch may not change this field"))
    if old.type != new.type:
        return
    old_params, new_params = old_task["params"], new_task["params"]
    changeable = []
    for key in _differing(old_params, new_params, ""):
        if key in CHANGEABLE[old.type]:
            changeable.append(key)
        else:
            found["issues"].append(_frozen((*path, "params", key), "a patch may not change this"))
    if not changeable:
        return
    if old.type == "compile":
        if in_result_chain:
            found["issues"].append(
                Issue(
                    "build-changed-after-result",
                    pointer(path),
                    "a simulate task of this build already completed",
                )
            )
        else:
            found["recompile"].append(tid)
    elif old.type == "simulate" and old.checkpoint and "seed" in changeable:
        found["issues"].append(
            Issue(
                "seed-changed-on-checkpoint-task",
                pointer((*path, "params", "seed")),
                "a checkpoint written under the old seed would be resumed under another",
            )
        )
    else:
        found["changed"].append(tid)


def patch_check(old: Plan, new: Plan, completed) -> PatchReport:
    """The verdict of replacing `old`, the plan a job runs, by `new`, given the completed task ids.

    Pure: neither plan nor `completed` is modified, and equal inputs give equal reports.
    A compile task is in the chain of the simulate tasks that name its `component` output, and a
    completed simulate task in a chain refuses a changed build.
    The report lists every changed task, and whether a paused task with a committed checkpoint can
    resume under changed code is left to the checkpoint contract.
    """
    completed = frozenset(completed)
    found = {"issues": [], "changed": [], "recompile": []}
    old_tasks = {t.id: t for t in old.tasks}
    new_tasks = {t.id: t for t in new.tasks}
    for tid in sorted(completed - old_tasks.keys()):
        found["issues"].append(
            Issue("unknown-completed-task", pointer(("tasks", tid)), "not a task of the plan")
        )
    for field in _differing(_dump(old), _dump(new), "tasks"):
        found["issues"].append(_frozen((field,), "a patch may not change this field"))
    for tid in sorted(new_tasks.keys() - old_tasks.keys()):
        found["issues"].append(_frozen(("tasks", tid), "a patch may not add a task"))
    for tid in sorted(old_tasks.keys() - new_tasks.keys()):
        found["issues"].append(_frozen(("tasks", tid), "a patch may not remove a task"))
    results = {tid for tid in completed if tid in old_tasks and old_tasks[tid].type == "simulate"}
    chain_of = chains(old)
    for tid in sorted(old_tasks.keys() & new_tasks.keys()):
        in_chain = bool(results & set(chain_of.get(tid, ())))
        _task_issues(tid, old_tasks[tid], new_tasks[tid], completed, in_chain, found)
    issues = ordered(found["issues"])
    return PatchReport(
        compatible=not issues,
        issues=issues,
        changed=tuple(sorted(found["changed"])),
        recompile=tuple(sorted(found["recompile"])),
    )
