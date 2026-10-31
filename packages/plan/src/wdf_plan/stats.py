"""What admission and placement read from a plan: its stats and the class hint."""

from wdf_jobdoc.plan_models import Plan, PlanStats


def _levels(plan: Plan) -> list[list]:
    """Tasks grouped by topological depth: a task is one level below its deepest dependency."""
    depth: dict[str, int] = {}
    levels: list[list] = []
    for task in plan.tasks:
        depth[task.id] = 1 + max((depth[d] for d in task.depends_on), default=-1)
        if depth[task.id] == len(levels):
            levels.append([])
        levels[depth[task.id]].append(task)
    return levels


def chains(plan: Plan) -> dict[str, list[str]]:
    """Each compile task with the simulate tasks that name its `component` output as an input.

    Only a data edge puts a task in a chain, so `depends_on` alone never does.
    """
    found: dict[str, list[str]] = {t.id: [] for t in plan.tasks if t.type == "compile"}
    for task in plan.tasks:
        if task.type == "simulate":
            for ref in task.inputs:
                if ref.output == "component" and ref.task in found:
                    found[ref.task].append(task.id)
    return {build: sorted(sims) for build, sims in found.items()}


def stats(plan: Plan, profile) -> PlanStats:
    """The statistics of a plan, the event estimate being the one number that reads `profile`.

    `critical_path_timeout_bound_s` is the longest path of run timeouts times one plus the
    retries, a bound along one path and not a wall-clock bound, since a job wider than
    `max_parallel` also waits.
    `max_width` is the widest topological level, a plain bound that is not the exact antichain.
    The peaks are the busiest level's demand when its `max_parallel` largest tasks run at once.
    """
    levels = _levels(plan)
    longest: dict[str, int] = {}
    for task in plan.tasks:
        before = max((longest[d] for d in task.depends_on), default=0)
        longest[task.id] = before + task.run_timeout_s * (1 + task.retries)

    def peak(field: str) -> int:
        return max(
            (
                sum(sorted((getattr(t, field) for t in level), reverse=True)[: plan.max_parallel])
                for level in levels
            ),
            default=0,
        )

    counts = {
        kind: sum(t.type == kind for t in plan.tasks)
        for kind in ("compile", "simulate", "aggregate")
    }
    return PlanStats(
        task_count=len(plan.tasks),
        counts=counts,
        chain_count=counts["compile"],
        max_width=max(map(len, levels), default=0),
        critical_path_timeout_bound_s=max(longest.values(), default=0),
        peak_cpu_milli=peak("cpu_milli"),
        peak_memory_mib=peak("memory_mib"),
        largest_cpu_milli=max((t.cpu_milli for t in plan.tasks), default=0),
        largest_memory_mib=max((t.memory_mib for t in plan.tasks), default=0),
        checkpoint_tasks=sum(t.checkpoint for t in plan.tasks),
        estimated_history_events=profile.history_events_per_job
        + sum(
            profile.history_events_per_task + profile.history_events_per_retry * t.retries
            for t in plan.tasks
        ),
    )


def class_hint(plan_stats: PlanStats, profile) -> str:
    """`short` when the critical path bound is within the profile threshold, `long` above it."""
    short, long = profile.classes
    return short if plan_stats.critical_path_timeout_bound_s <= profile.class_threshold_s else long
