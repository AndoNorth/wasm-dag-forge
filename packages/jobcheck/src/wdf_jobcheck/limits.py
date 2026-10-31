"""The limits pass: counts, per task ceilings and the numbers that come from the compiled plan.

The plan size, the critical path bound and the history estimate are read from the plan library,
so submit, placement and the workflow share one number for each.
"""

from wdf_jobdoc import Document, Issue
from wdf_jobdoc.issues import pointer
from wdf_plan import canonical_bytes, compile_plan, stats

__all__ = ["check_limits"]


def check_limits(document: Document, profile) -> tuple[Issue, ...]:
    issues = []
    if len(document.tasks) > profile.max_tasks:
        issues.append(
            Issue(
                "too-many-tasks",
                "/tasks",
                f"{len(document.tasks)} tasks, at most {profile.max_tasks} are allowed",
            )
        )
    if document.max_parallel is not None and document.max_parallel > profile.max_parallel:
        issues.append(
            Issue(
                "max-parallel-exceeded",
                "/max_parallel",
                f"{document.max_parallel}, at most {profile.max_parallel} is allowed",
            )
        )
    for task_id, task in document.tasks.items():
        if task.type == "compile":
            continue
        if task.run_timeout_s > profile.max_run_timeout_s:
            issues.append(
                Issue(
                    "run-timeout-too-long",
                    pointer(["tasks", task_id, "run_timeout_s"]),
                    f"{task.run_timeout_s} s, at most {profile.max_run_timeout_s} s per attempt",
                )
            )
        if task.retries > profile.max_retries:
            issues.append(
                Issue(
                    "retries-too-many",
                    pointer(["tasks", task_id, "retries"]),
                    f"{task.retries} retries, at most {profile.max_retries} are allowed",
                )
            )
    return tuple(issues) + _plan_limits(document, profile)


def _plan_limits(document: Document, profile) -> tuple[Issue, ...]:
    try:
        plan = compile_plan(document, profile)
    except ValueError:
        # A graph fault is the graph pass's finding, and there is no plan to measure.
        return ()
    issues = []
    size = len(canonical_bytes(plan))
    if size > profile.max_plan_bytes:
        issues.append(
            Issue(
                "plan-too-large",
                "",
                f"the plan is {size} canonical bytes, at most {profile.max_plan_bytes} are allowed",
            )
        )
    measured = stats(plan, profile)
    if measured.critical_path_timeout_bound_s > profile.critical_path_limit_s:
        issues.append(
            Issue(
                "critical-path-too-long",
                "",
                f"the longest path bound is {measured.critical_path_timeout_bound_s} s, "
                f"at most {profile.critical_path_limit_s} s",
            )
        )
    if measured.estimated_history_events > profile.max_history_events:
        issues.append(
            Issue(
                "history-budget-exceeded",
                "",
                f"about {measured.estimated_history_events} history events, "
                f"at most {profile.max_history_events} are allowed",
            )
        )
    return tuple(issues)
