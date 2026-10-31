"""The resource pass: what a task asks for against what one slot of its pool offers."""

from wdf_jobdoc import Document, Issue
from wdf_jobdoc.issues import pointer
from wdf_jobdoc.profile import Slot
from wdf_jobdoc.registry import TASK_TYPES

__all__ = ["check_resources", "pool_budget"]


def pool_budget(profile, task_type: str) -> Slot:
    """The most one task of `task_type` may ask for.

    Placement reads the same number to decide a task is unschedulable, so the rule that refuses a
    document at submit and the one that refuses a task at placement cannot drift apart.
    A profile with ceilings lifts every pool to them, and any other uses the slot of its pool.
    """
    if profile.ceiling_cpu_milli is not None and profile.ceiling_memory_mib is not None:
        return Slot(cpu_milli=profile.ceiling_cpu_milli, memory_mib=profile.ceiling_memory_mib)
    return profile.pools.slot(TASK_TYPES[task_type]["pool"])


def check_resources(document: Document, profile) -> tuple[Issue, ...]:
    """Compile tasks ask for nothing and take the slot, so only the other types are checked."""
    issues = []
    for task_id, task in document.tasks.items():
        if task.type == "compile":
            continue
        budget = pool_budget(profile, task.type)
        if task.cpu_milli > budget.cpu_milli:
            issues.append(
                Issue(
                    "cpu-exceeds-slot",
                    pointer(["tasks", task_id, "cpu"]),
                    f"{task.cpu_milli}m is above the {budget.cpu_milli}m of a {task.type} slot",
                )
            )
        if task.memory_mib > budget.memory_mib:
            issues.append(
                Issue(
                    "memory-exceeds-slot",
                    pointer(["tasks", task_id, "memory_mib"]),
                    f"{task.memory_mib}Mi is above the {budget.memory_mib}Mi of a {task.type} slot",
                )
            )
    return tuple(issues)
